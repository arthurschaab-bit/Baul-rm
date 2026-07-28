# -*- coding: utf-8 -*-
"""
02_features.py
Akustische Merkmals-Extraktion und heuristische Laermquellen-Zuordnung.
WAV: mono, 16 kHz, int16, ~4-5 s.

Wichtig: Die Zuordnung ist eine REGELBASIERTE SCHAETZUNG aus Audiomerkmalen
(kein trainiertes Modell). Sie dient als Vorschlag, der manuell zu pruefen ist.
Kategorien orientieren sich an den Beispielen des Nutzers:
  Sprache      - menschliche Stimme
  Bohren/Impuls- impulshaft/hochfrequent (Bohrer, Schlagen, Pickel)
  Motor/Bagger - tieffrequenter Dauer-/Motorklang (Bagger, LKW, Diesel, Aggregat)
  Fahrzeug     - breitbandige Vorbeifahrt (Auto/Verkehr) mit An-/Abschwellen
  Sonstiges    - nicht eindeutig
"""
import io, os, wave, math
import numpy as np
from scipy import signal

SR = 16000

def load_wav_bytes(b):
    w = wave.open(io.BytesIO(b), 'rb')
    sr = w.getframerate(); ch = w.getnchannels(); sw = w.getsampwidth()
    n = w.getnframes()
    raw = w.readframes(n); w.close()
    if sw == 2:
        x = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    elif sw == 1:
        x = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128) / 128.0
    else:
        x = np.frombuffer(raw, dtype=np.int32).astype(np.float32) / 2147483648.0
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    return x, sr

def load_wav_path(p):
    with open(p, 'rb') as f:
        return load_wav_bytes(f.read())

def features(x, sr=SR):
    f = {}
    n = len(x)
    if n < sr // 4:        # < 0.25 s -> unbrauchbar
        return None
    x = x - np.mean(x)
    rms = float(np.sqrt(np.mean(x**2)) + 1e-12)
    peak = float(np.max(np.abs(x)) + 1e-12)
    f['rms'] = rms
    f['crest'] = peak / rms                      # Impulsivitaet (Spitze/Effektiv)

    # --- Hüllkurve (Frame-Energie, 20 ms Hop)
    hop = int(0.02 * sr)
    frames = [x[i:i+hop] for i in range(0, n - hop, hop)]
    env = np.array([np.sqrt(np.mean(fr**2) + 1e-12) for fr in frames])
    env_n = env / (np.mean(env) + 1e-12)
    f['env_kurtosis'] = float(((env_n - env_n.mean())**4).mean() / (env_n.var()**2 + 1e-12))
    f['env_cv'] = float(env.std() / (env.mean() + 1e-12))   # Schwankung der Lautstärke

    # Form der Hüllkurve: Vorbeifahrt = ein Anstieg/Abfall (Peak mittig)
    if len(env) >= 5:
        peak_pos = int(np.argmax(env)) / (len(env) - 1)
        f['env_peak_pos'] = float(peak_pos)
        # Anteil "ein einzelner Berg": Korrelation mit Dreiecksfenster
        tri = signal.windows.triang(len(env))
        f['env_triang_corr'] = float(np.corrcoef(env, tri)[0, 1])
    else:
        f['env_peak_pos'] = 0.5; f['env_triang_corr'] = 0.0

    # --- Spektrum (Welch)
    fr_, psd = signal.welch(x, sr, nperseg=min(2048, n))
    psd = psd + 1e-15
    psdn = psd / psd.sum()
    f['centroid'] = float((fr_ * psdn).sum())
    f['rolloff85'] = float(fr_[np.searchsorted(np.cumsum(psdn), 0.85)])
    # Spektrale Flachheit: tonal (klein) vs rauschartig/breitbandig (gross)
    f['flatness'] = float(np.exp(np.mean(np.log(psd))) / np.mean(psd))
    f['dom_freq'] = float(fr_[np.argmax(psd)])

    def band(lo, hi):
        m = (fr_ >= lo) & (fr_ < hi)
        return float(psd[m].sum() / psd.sum())
    f['e_sub'] = band(0, 250)        # tiefes Dröhnen (Motor/Bagger)
    f['e_low'] = band(250, 600)
    f['e_speech'] = band(300, 3400)  # Sprachband
    f['e_mid'] = band(600, 2000)
    f['e_high'] = band(2000, 6000)
    f['e_vhigh'] = band(6000, 8000)  # Zischen/Bohrer-Whine

    # --- Tonalität: stärkster Peak relativ zur Umgebung
    f['tonal_peak'] = float(psd.max() / (np.median(psd) + 1e-15))

    # --- Sprach-Rhythmus: Modulation der Hüllkurve bei 2-8 Hz (Silben)
    env_rate = sr / hop
    if len(env_n) >= 16:
        e = env_n - env_n.mean()
        E = np.abs(np.fft.rfft(e * np.hanning(len(e))))
        ef = np.fft.rfftfreq(len(e), 1.0 / env_rate)
        syl = (ef >= 2) & (ef <= 8)
        tot = (ef >= 0.5) & (ef <= 20)
        f['syllabic'] = float(E[syl].sum() / (E[tot].sum() + 1e-12))
        # Rhythmus/Periodizität (Maschinentakt): Peak im Mod-Spektrum 0.5-20 Hz
        if E[tot].sum() > 0:
            idx = np.where(tot)[0]
            f['rhythm_strength'] = float(E[idx].max() / (E[idx].mean() + 1e-12))
            f['rhythm_freq'] = float(ef[idx][np.argmax(E[idx])])
        else:
            f['rhythm_strength'] = 0.0; f['rhythm_freq'] = 0.0
    else:
        f['syllabic'] = 0.0; f['rhythm_strength'] = 0.0; f['rhythm_freq'] = 0.0

    # --- Grundton (Stimme/Brummton) per FFT-Autokorrelation 80-300 Hz
    seg = x[:min(n, sr)]
    ac = signal.correlate(seg, seg, mode='full', method='fft')[len(seg)-1:]
    ac = ac / (ac[0] + 1e-12)
    lo, hi = int(sr/300), int(sr/80)
    if hi < len(ac):
        f['pitch_strength'] = float(np.max(ac[lo:hi]))
        f['pitch_hz'] = float(sr / (lo + int(np.argmax(ac[lo:hi]))))
    else:
        f['pitch_strength'] = 0.0; f['pitch_hz'] = 0.0

    # Zero-Crossing-Rate
    f['zcr'] = float(np.mean(np.abs(np.diff(np.sign(x)))) / 2)
    return f


def classify(f):
    """Gibt (label, konfidenz 0..1, begruendung) zurueck."""
    if f is None:
        return ("Sonstiges", 0.0, "kein Audio")
    scores = {}

    # Sprache: braucht sprachSPEZIFISCHE Hinweise (Silbentakt 2-8 Hz ODER Grundton),
    # nicht nur Energie im breiten 300-3400-Hz-Band (das ist bei fast jedem Lärm voll).
    has_pitch = (0.30 < f['pitch_strength'] < 0.97 and 90 < f['pitch_hz'] < 300)
    s_speech = 0.0
    if f['syllabic'] > 0.45:  s_speech += 1.2     # Silbenrhythmus = Primärindiz
    if has_pitch:             s_speech += 1.0     # stimmhafter Grundton = Primärindiz
    if f['e_speech'] > 0.60 and f['e_sub'] < 0.20 and f['e_vhigh'] < 0.15:
        s_speech += 0.6                            # Energie im Stimmband konzentriert
    if 400 < f['centroid'] < 2000: s_speech += 0.3
    if f['e_sub'] > 0.35:     s_speech -= 1.0      # viel Tiefbass -> eher Motor
    if f['e_high'] + f['e_vhigh'] > 0.5: s_speech -= 0.8  # viel HF -> eher Bohren
    # Gate: ohne Primärindiz kein Sprache-Verdacht
    if f['syllabic'] <= 0.45 and not has_pitch:
        s_speech *= 0.35
    scores['Sprache'] = s_speech

    # Motor/Bagger: tieffrequent, dauerhaft (geringe Impulsivität), niedriger Centroid
    s_motor = 0.0
    if f['e_sub'] > 0.30:     s_motor += 1.0
    if f['centroid'] < 700:   s_motor += 1.0
    if f['crest'] < 6:        s_motor += 0.5    # gleichmäßig
    if f['env_cv'] < 0.6:     s_motor += 0.5
    if f['rolloff85'] < 1500: s_motor += 0.5
    if 0.3 < f['rhythm_freq'] < 5 and f['rhythm_strength'] > 6: s_motor += 0.4  # Diesel-Takt
    scores['Motor/Bagger'] = s_motor

    # Bohren/Impuls: hohe Impulsivität ODER hochfrequenter Dauer-Whine, Taktung
    s_drill = 0.0
    if f['crest'] > 7:        s_drill += 0.8
    if f['env_kurtosis'] > 6: s_drill += 0.6
    if f['e_high'] + f['e_vhigh'] > 0.45: s_drill += 1.0
    if f['centroid'] > 2200:  s_drill += 0.8
    if f['tonal_peak'] > 50 and f['dom_freq'] > 1500: s_drill += 0.6  # Bohrer-Pfeifen
    if 2 < f['rhythm_freq'] < 15 and f['rhythm_strength'] > 7: s_drill += 0.5
    scores['Bohren/Impuls'] = s_drill

    # Fahrzeug/Vorbeifahrt: breitbandig, ein Anschwellen/Abschwellen, mittlerer Centroid
    s_car = 0.0
    if f['flatness'] > 0.25:  s_car += 0.5      # breitbandig/rauschig
    if f['env_triang_corr'] > 0.4 and 0.25 < f['env_peak_pos'] < 0.75: s_car += 1.0
    if 700 < f['centroid'] < 2500: s_car += 0.6
    if f['e_high'] > 0.2 and f['e_sub'] < 0.35: s_car += 0.4
    if f['syllabic'] < 0.4:   s_car += 0.2
    scores['Fahrzeug'] = s_car

    label = max(scores, key=scores.get)
    top = scores[label]
    vals = sorted(scores.values(), reverse=True)
    margin = (vals[0] - vals[1]) if len(vals) > 1 else vals[0]
    # Konfidenz: Höhe des Top-Scores + Abstand zum zweiten
    conf = max(0.0, min(1.0, 0.20 * top + 0.35 * margin))
    if top < 1.0:
        label = "Sonstiges"
        conf = min(conf, 0.25)
    reason = " ".join(f"{k}={v:.1f}" for k, v in sorted(scores.items(), key=lambda kv: -kv[1]))
    return (label, round(conf, 2), reason)


if __name__ == "__main__":
    import glob, sys
    folder = sys.argv[1] if len(sys.argv) > 1 else "."
    fs = sorted(glob.glob(os.path.join(folder, "*.wav")))
    if not fs:
        print("keine WAV in", folder); sys.exit()
    print(f"{len(fs)} WAV in {folder} - Stichprobe:")
    hdr = ["WAV","Label","Konf","centroid","e_sub","e_speech","e_high","crest","syllabic","pitch_str","rhythm_f","flatness"]
    print(("{:<26}{:<14}{:>5} " + "{:>9}"*9).format(*hdr))
    import random
    sample = fs if len(fs) <= 25 else random.sample(fs, 25)
    for p in sorted(sample):
        x, sr = load_wav_path(p)
        feat = features(x, sr)
        lab, conf, _ = classify(feat)
        if feat is None: continue
        print(("{:<26}{:<14}{:>5.2f} " + "{:>9.2f}"*9).format(
            os.path.basename(p)[:25], lab, conf,
            feat['centroid'], feat['e_sub'], feat['e_speech'], feat['e_high'],
            feat['crest'], feat['syllabic'], feat['pitch_strength'],
            feat['rhythm_freq'], feat['flatness']))

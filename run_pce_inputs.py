"""Normalize local CSV/SQLite inputs without publishing identity or measurements."""
import argparse,csv,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent/'src'))
from baul_rm.report_inputs import collect
from baul_rm.report_core import require_external_path

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path,required=True)
    parser.add_argument('--legacy-input',type=Path)
    parser.add_argument('--database',type=Path,action='append',default=[])
    parser.add_argument('--session-manifest',type=Path)
    parser.add_argument('--start',required=True);parser.add_argument('--end',required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    checkout=Path(__file__).resolve().parent
    for path in [args.input,args.output,*args.database]+([args.legacy_input] if args.legacy_input else [])+([args.session_manifest] if args.session_manifest else []):
        require_external_path(path,checkout)
    args.output.mkdir(parents=True,exist_ok=True)
    data,audit,manifest=collect(args.input,args.start,args.end,args.database,args.legacy_input,args.session_manifest)
    data.to_csv(args.output/'selected_seconds.csv',sep=';',encoding='utf-8-sig')
    (args.output/'audit.json').write_text(json.dumps({'audit':audit,'manifest':manifest},ensure_ascii=False,indent=2),encoding='utf-8')
    print(f'{len(data)} selected PCE seconds written locally')

"""Run the public calculation core with a private local report profile."""
import argparse
import sys
import os
import json
import time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent/'src'))
from baul_rm import report_core

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile',type=Path,required=True,help='Private local Python profile outside this checkout')
    parser.add_argument('--meteo-mode',choices=['ausweisen','ausschliessen'],default='ausweisen',help='Preferred variant; both variants are always required')
    parser.add_argument('--result-file',type=Path,help='Private result pointer for the existing automatic pipeline')
    args=parser.parse_args()
    os.environ['BAUL_RM_METEO_MODE']=args.meteo_mode
    started_ns=time.time_ns()
    result=report_core.load_private_profile(args.profile,Path(__file__).resolve().parent).run(report_core)
    from baul_rm.meteo_analysis import require_both_variants
    if result is None:
        raise ValueError('Profile must return its private output directory; both weather variants are required')
    output=report_core.require_external_path(result,Path(__file__).resolve().parent)
    require_both_variants(output,created_after_ns=started_ns)
    if args.result_file:
        pointer=report_core.require_external_path(args.result_file,Path(__file__).resolve().parent)
        pointer.write_text(json.dumps({'output_directory':str(output)}),encoding='utf-8')

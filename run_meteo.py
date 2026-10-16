"""Download and verify public DWD observations; never accepts private case data."""
import argparse
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent/'src'))
from baul_rm.meteo_dwd import fetch_weather

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--start',required=True)
    parser.add_argument('--end',required=True)
    parser.add_argument('--station',default='03379')
    parser.add_argument('--output',type=Path,default=Path(__file__).resolve().parent/'data/meteo')
    parser.add_argument('--refresh',action='store_true')
    args=parser.parse_args()
    print(fetch_weather(args.output,args.start,args.end,args.station,args.refresh))

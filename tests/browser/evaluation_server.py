"""Isolated browser fixture: real UI/router, temporary data, no real engine or API keys."""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT/'src'), str(ROOT/'tests')]

import cv2
import numpy as np
import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
import api_routes
import config_store
import eval_store
from database import CharacterDB
from test_api import FakeKeys, FakeRunner


def build(data):
    cfg=data/'config.json'; db=CharacterDB(data/'fixture.db')
    db.add_show('fixture','Fixture');db.update_show_status('fixture','ready')
    db.add_character('mara','fixture','Mara Ellis',first_season=1,first_episode=1)
    db.add_character('ben','fixture','Ben Davis',first_season=3,first_episode=1)
    config_store.update_config(cfg,{'eval_mode':True,'show_id':'fixture','season':1,'episode':2})
    _,raw=cv2.imencode('.jpg',np.full((300,500,3),150,np.uint8))
    fid=eval_store.save_capture(data,cfg,raw.tobytes(),500,300,{
        'show_id':'fixture','season':1,'episode':2,'progress':.2,
        'thresholds':{'detector':.5,'similarity':.5,'margin':0},
        'gallery':{'character_ids':['mara','ben']},
        'faces':[{'bbox':[100,50,200,200],'detector_confidence':.99,'displayed_character_id':'mara'}]})
    config_store.update_config(cfg,{'eval_mode':False})
    app=FastAPI()
    api_routes.install(app,api_routes.ApiDeps(config_path=cfg,env_path=data/'.env',get_db=lambda:db,
        keystore=FakeKeys(TMDB_API_KEY='test'),key_check=None,runner=FakeRunner(),
        tmdb_get=lambda *a:{},permissions=lambda:{'screen_recording':True},eval_data_dir=lambda:data))
    app.mount('/ui',StaticFiles(directory=str(ROOT/'src/ui'),html=True))
    return app


if __name__=='__main__':
    with tempfile.TemporaryDirectory(prefix='goldfish-eval-browser-') as d:
        uvicorn.run(build(Path(d)),host='127.0.0.1',port=int(sys.argv[1]),log_level='warning')

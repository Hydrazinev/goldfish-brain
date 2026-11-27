import base64
import json
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import api_routes
import config_store
import eval_metrics
import eval_store
import server
from database import CharacterDB
from test_api import FakeKeys, FakeRunner


@pytest.fixture
def env(tmp_path, monkeypatch):
    data = tmp_path / 'data'
    data.mkdir()
    cfg = data / 'config.json'
    db = CharacterDB(tmp_path / 'test.db')
    db.add_show('s', 'Show'); db.update_show_status('s', 'ready')
    db.add_character('a', 's', 'Mara Ellis', first_season=1, first_episode=1)
    db.add_character('b', 's', 'Ben Davis', first_season=3, first_episode=1)
    db.add_character_arc('a', 1, 1, status='alive', current_arc='A safe fact')
    config_store.update_config(cfg, {'show_id':'s','season':1,'episode':2,'positions':{'s':{'season':1,'episode':2}}})
    app = FastAPI()
    api_routes.install(app, api_routes.ApiDeps(config_path=cfg, env_path=tmp_path/'.env', get_db=lambda:db,
        keystore=FakeKeys(), key_check=None, runner=FakeRunner(), tmdb_get=lambda *a:{},
        permissions=lambda:{}, eval_data_dir=lambda:data))
    client = TestClient(app, base_url='http://127.0.0.1')
    monkeypatch.setattr(server, '_DATA_DIR', data)
    monkeypatch.setattr(server, '_CONFIG_PATH', cfg)
    monkeypatch.setattr(server, 'db', db, raising=False)
    class Recogniser:
        def detect_faces(self, image):
            return [(None, [1.,2.,8.,12.], SimpleNamespace(det_score=.99))]
        def get_embedding(self, image, face): return np.array([1])
    class Index:
        _ids = ['a','b']
        def search(self, emb, top_k=5): return [('a',.9),('b',.2)]
    monkeypatch.setattr(server, 'recogniser', Recogniser(), raising=False)
    monkeypatch.setitem(server.indexes, 's', Index())
    _, raw = cv2.imencode('.jpg', np.full((16,16,3),120,np.uint8))
    request = {'image':base64.b64encode(raw).decode(),'show_id':'s','season':1,'episode':2,'progress':.3}
    def capture():
        response = TestClient(server.app).post('/identify',json=request)
        assert response.status_code == 200
        return response
    def enable():
        r=client.post('/api/eval/settings',json={'eval_mode':True,'acknowledge_full_display':True,'max_mb':10})
        assert r.status_code==200
    return SimpleNamespace(data=data, cfg=cfg, db=db, client=client, capture=capture, enable=enable, raw=raw.tobytes())


def first(env):
    return eval_store.list_frames(env.data)[0]['id']


def test_capture_is_off_by_default_and_acknowledgement_required(env):
    env.capture()
    assert not (env.data/'eval/frames').exists()
    assert env.client.get('/api/state').json()['eval_mode'] is False
    assert env.client.post('/api/eval/settings',json={'eval_mode':True}).status_code==400
    env.enable()
    assert env.client.get('/api/state').json()['eval_mode'] is True


def test_exact_bytes_full_provenance_and_no_predictions_before_reveal(env):
    env.enable();env.capture();fid=first(env)
    frame=eval_store.load_frame(env.data,fid)
    assert eval_store.frame_path(env.data,fid,'.jpg').read_bytes()==env.raw
    assert frame['schema_version']==1 and frame['progress']==.3
    assert frame['faces'][0]['candidates'][0]['score']==.9
    assert frame['faces'][0]['bbox']==[1,2,8,12]
    assert frame['model']['det_size']==[640,640]
    assert frame['gallery']['character_ids']==['a','b']
    assert frame['calibration_version'] and frame['app']['commit']
    detail=env.client.get(f'/api/eval/frames/{fid}').json()
    assert 'predictions' not in detail
    assert detail['choices']==[{'id':'a','name':'Mara Ellis'}]
    assert env.client.get(f'/api/eval/frames/{fid}/image').content==env.raw
    assert env.client.get(f'/api/eval/frames/{fid}/image',headers={'Origin':'https://evil.example'}).status_code==403
    assert env.client.get('/api/eval/frames/not-a-uuid/image').status_code==404


def test_failed_capture_cannot_fail_identification(env,monkeypatch):
    env.enable()
    monkeypatch.setattr(eval_store,'save_capture',lambda *a,**k: (_ for _ in ()).throw(OSError('disk full')))
    assert env.capture().json()['characters'][0]['character_id']=='a'


def test_zero_detection_frames_are_saved(env,monkeypatch):
    env.enable()
    monkeypatch.setattr(server.recogniser,'detect_faces',lambda image:[])
    env.capture()
    assert eval_store.load_frame(env.data,first(env))['faces']==[]


def test_labels_are_append_only_revisioned_and_exposure_is_server_recorded(env):
    env.enable();env.capture();fid=first(env)
    body={'base_revision':0,'faces':[{'bbox':[1,2,8,12],'identity':'a','tags':['profile']}],'complete':True}
    result=env.client.post(f'/api/eval/frames/{fid}/labels',json=body)
    assert result.status_code==200
    assert result.json()['label']['faces'][0]['bbox_normalized']==[1/16,2/16,8/16,12/16]
    assert result.json()['label']['prediction_revealed'] is False
    assert env.client.post(f'/api/eval/frames/{fid}/labels',json=body).status_code==409
    assert env.client.post(f'/api/eval/frames/{fid}/reveal',json={'base_revision':1}).status_code==200
    body['base_revision']=2;body['prediction_revealed']=False
    assert env.client.post(f'/api/eval/frames/{fid}/labels',json=body).json()['label']['prediction_revealed'] is True
    assert len(eval_store.events(env.data,fid))==3


def test_label_validation_and_no_faces(env):
    env.enable();env.capture();fid=first(env);path=f'/api/eval/frames/{fid}/labels'
    bad={'base_revision':0,'complete':True,'faces':[{'bbox':[-1,0,5,5],'identity':'a'}]}
    assert env.client.post(path,json=bad).status_code==400
    assert env.client.post(path,json={'base_revision':0,'complete':True,'faces':[]}).status_code==400
    assert env.client.post(path,json={'base_revision':0,'complete':True,'no_faces':True,'faces':[]}).status_code==200


def test_cap_and_delete_do_not_touch_ask_results_or_library(env):
    env.enable();env.capture()
    ask_file=env.data/'eval/ask_results.json';ask_file.write_text('keep')
    root=env.data/'eval/frames'
    (root/'quota-test').write_bytes(b'x'*(10*1024*1024))
    env.capture();assert len(eval_store.list_frames(env.data))==1
    assert 'cap reached' in env.client.get('/api/eval/status').json()['last_capture']
    assert env.client.post('/api/eval/delete',json={'confirmation':'no'}).status_code==400
    assert env.client.post('/api/eval/delete',json={'confirmation':'DELETE'}).status_code==200
    assert ask_file.read_text()=='keep' and env.db.get_character('a')
    assert config_store.read_config(env.cfg)['eval_mode'] is False


def test_corrupt_config_is_never_overwritten(env):
    env.cfg.write_text('{broken')
    assert env.client.post('/api/eval/settings',json={'eval_mode':False}).status_code==409
    assert env.cfg.read_text()=='{broken'


def test_scoring_includes_misses_and_uses_geometry_not_identity():
    frame={'id':'1','show_id':'s','season':1,'episode':1,'thresholds':{'detector':.5},'faces':[
        {'bbox':[0,0,10,10],'detector_confidence':.9,'displayed_character_id':'a'},
        {'bbox':[20,0,30,10],'detector_confidence':.9,'displayed_character_id':'a'}]}
    label={'faces':[{'bbox':[0,0,10,10],'identity':'a'},
                    {'bbox':[20,0,30,10],'identity':'extra'},
                    {'bbox':[40,0,50,10],'identity':'b'}]}
    counts,_,_=eval_metrics.frame_counts(frame,label)
    metrics=eval_metrics.estimates(counts)
    assert metrics['precision']==.5 and metrics['identification_recall']==.5
    assert metrics['detection_recall']==pytest.approx(2/3)
    assert metrics['extra_false_match_rate']==1
    assert counts['known_cast_faces']==2


def test_uncertain_faces_do_not_inflate_identity_precision():
    frame={'thresholds':{'detector':.5},'faces':[{'bbox':[0,0,10,10],'displayed_character_id':'a'}]}
    counts,_,_=eval_metrics.frame_counts(frame,{'faces':[{'bbox':[0,0,10,10],'identity':'cant_determine'}]})
    assert counts['shown_names']==0 and counts['faces']==1 and counts['detected_faces']==1


def test_clustered_intervals_and_frozen_split(tmp_path):
    import sys
    sys.path.insert(0,str(Path(__file__).parent.parent/'scripts'))
    from score_recognition import freeze,score
    cfg=tmp_path/'config.json';config_store.update_config(cfg,{'eval_mode':True})
    for i in range(3):
        fid=eval_store.save_capture(tmp_path,cfg,b'image',100,100,{'show_id':f's{i}','season':1,'episode':1,
            'gallery':{'character_ids':['a']},'thresholds':{'detector':.5},'faces':[]})
        eval_store.append_event(tmp_path,cfg,fid,{'base_revision':0,'faces':[{'bbox':[0,0,10,10],'identity':'a'}],'complete':True})
    path=tmp_path/'split.json';frozen=freeze(tmp_path,path,seed=4)
    with pytest.raises(FileExistsError):freeze(tmp_path,path)
    tune=score(tmp_path,path,repeats=200);test=score(tmp_path,path,final=True,repeats=200)
    assert tune['partition']=='tune' and test['partition']=='test'
    assert {x['frame_id'] for x in tune['labels']}.isdisjoint({x['frame_id'] for x in test['labels']})
    assert tune['overall']['metrics']['identification_recall']==0
    assert sum(1 for f in frozen['frames'].values() if f['partition']=='test')==1


def test_replay_uses_live_assignment_and_withholds_hidden_character_ids():
    import sys
    sys.path.insert(0,str(Path(__file__).parent.parent/'scripts'))
    from replay_recognition import replay_frame
    class R:
        def detect_faces(self,image):return [(None,[0,0,10,10],SimpleNamespace(det_score=.9))]
        def get_embedding(self,image,face):return np.array([1])
    class I:
        def search(self,emb,top_k=5):return [('b',.9),('a',.2)]
    frame={'gallery':{'character_ids':['a','b']},'visible_character_ids':['a']}
    rows=replay_frame(frame,None,R(),I(),{'similarity':.5,'margin':0,'detector':.5})
    assert rows[0]['assigned_character_id']=='b'
    assert rows[0]['displayed_character_id'] is None


def test_reveal_never_exposes_hidden_slug_without_explicit_opt_in(env):
    env.enable();env.capture();fid=first(env)
    p=eval_store.frame_path(env.data,fid)
    info=json.loads(p.read_text());info['faces'][0]['displayed_character_id']='b';p.write_text(json.dumps(info))
    result=env.client.post(f'/api/eval/frames/{fid}/reveal',json={'base_revision':0}).json()
    assert result['predictions'][0]['character_id'] is None
    assert 'Ben Davis' not in str(result)
    full=env.client.get(f'/api/eval/frames/{fid}?all=true').json()
    assert full['predictions'][0]['name']=='Ben Davis'

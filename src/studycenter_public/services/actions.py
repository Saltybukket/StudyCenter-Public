"""Bounded atomic personal actions with persistent UUID receipts and safe undo."""
import hashlib
import json
from uuid import UUID, uuid4

from ..storage.db import encode, now
from ..domain.validation import FIELDS as DOMAIN_FIELDS, validate_values
from .reminders import validate_personal_reminder, personal_reminder_state, write_personal_reminder

FIELDS = {'title','body','description','date','time','end_date','end_time','due_at','date_meaning','obligation','source_state','pinned','ignored'}
TYPES = {'create_note','create_task','create_event','update','set_status','set_override','decide_source','delete','set_reminder'}


def request_uuid(value):
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError('A UUID request identifier is required') from exc


class ActionService:
    def __init__(self,db):
        self.db=db

    def _validate(self,actions):
        if not isinstance(actions,list) or not 1<=len(actions)<=100:
            raise ValueError('A batch contains between 1 and 100 actions')
        if len(encode(actions))>100_000:
            raise ValueError('Action batch exceeds the size bound')
        for action in actions:
            if not isinstance(action,dict) or action.get('type') not in TYPES:
                raise ValueError('Unsupported action')
            if action['type'].startswith('create_'):
                if set(action)-({'type','fields'} | FIELDS):raise ValueError('Unknown create action fields')
                if not isinstance(action.get('fields',{}),dict):raise ValueError('Personal fields must be a mapping')
                validate_values({k:v for k,v in action.items() if k not in ('type','fields')} | action.get('fields',{}))
                title=action.get('title',action.get('fields',{}).get('title',''))
                if not isinstance(title,str) or not title.strip() or len(title)>240:
                    raise ValueError('A bounded title is required')
            elif not isinstance(action.get('item_id',action.get('id')),int):
                raise ValueError('An integer item ID is required')
            if action['type'] in ('update','set_override'):
                if not isinstance(action.get('fields'),dict) or not action['fields'] or set(action['fields'])-FIELDS:
                    raise ValueError('Unsupported override fields')
                validate_values(action['fields'])
            if action['type']=='set_status' and action.get('status') not in ('open','planned','done','ignored'):
                raise ValueError('Unsupported personal status')
            if action['type']=='set_reminder':
                if set(action)-{'type','item_id','id','enabled','offsets'} or not {'enabled','offsets'}<=set(action) or type(action.get('item_id',action.get('id'))) is not int:
                    raise ValueError('Reminder action requires item ID, enabled and bounded offsets')
                validate_personal_reminder({key:action[key] for key in ('enabled','offsets')})
            if action['type']=='decide_source':
                if action.get('field') not in FIELDS or 'value' not in action or not isinstance(action.get('document_id'),int) or not action.get('reason','').strip():
                    raise ValueError('A source-bound decision needs field, value, document and reason')
                validate_values({action['field']:action['value']})
        return actions

    def preview(self,actions):
        actions=self._validate(actions)
        return {'preview':True,'count':len(actions),'actions':json.loads(encode(actions)),'versions':{str(a.get('item_id',a.get('id'))):self.db.item(a.get('item_id',a.get('id')))['version'] for a in actions if not a['type'].startswith('create_') and self.db.item(a.get('item_id',a.get('id'))) is not None}}

    def _snapshot(self,con,item_id):
        row=self.db.one('SELECT * FROM items WHERE id=?',(item_id,),con)
        if not row:return None
        return {'row':row,'decisions':self.db.rows('SELECT * FROM decisions WHERE item_id=? ORDER BY field',(item_id,),con),
                'reminder':personal_reminder_state(self.db,con,item_id)}

    def _existing(self,con,request_id,body_hash):
        existing=self.db.one('SELECT * FROM action_receipts WHERE request_id=?',(request_id,),con)
        if existing:
            if existing['body_hash']!=body_hash:
                raise ValueError('UUID was already used with a different request body')
            return json.loads(existing['receipt'])

    def execute(self,actions,request_id,expected_versions=None):
        actions=self._validate(actions);request_id=request_uuid(request_id)
        body_hash=hashlib.sha256(encode({'actions':actions,'expected_versions':expected_versions}).encode()).hexdigest()
        with self.db.connection() as con:
            con.execute('BEGIN IMMEDIATE')
            existing=self._existing(con,request_id,body_hash)
            if existing:return existing
            before={};changed=[]
            for action in actions:
                kind=action['type']
                if kind.startswith('create_'):
                    payload={k:v for k,v in action.items() if k not in ('type','fields')} | action.get('fields',{}) | {'kind':kind[7:]}
                    item_id=self.db._create_item(con,payload,'personal:'+str(uuid4()))
                    before[str(item_id)]=None
                else:
                    item_id=action.get('item_id',action.get('id'));snap=self._snapshot(con,item_id)
                    if not snap or snap['row']['deleted']:
                        raise ValueError('Item does not exist')
                    if str(item_id) not in before:
                        before[str(item_id)]=snap
                        expected=(expected_versions or {}).get(str(item_id),(expected_versions or {}).get(item_id))
                        if expected is not None and snap['row']['version']!=expected:
                            raise ValueError('Item version changed; preview again')
                    if kind=='set_status':
                        con.execute('UPDATE items SET status=? WHERE id=?',(action['status'],item_id))
                    elif kind in ('update','set_override'):
                        overrides=json.loads(snap['row']['overrides']) | action['fields']
                        con.execute('UPDATE items SET overrides=? WHERE id=?',(encode(overrides),item_id))
                    elif kind=='delete':
                        con.execute('UPDATE items SET deleted=1 WHERE id=?',(item_id,))
                    elif kind=='set_reminder':
                        write_personal_reminder(self.db,con,item_id,{'present':True,'value':{key:action[key] for key in ('enabled','offsets')}})
                    elif kind=='decide_source':
                        doc=self.db.one('SELECT d.hash FROM documents d JOIN item_evidence e ON e.document_id=d.id WHERE e.item_id=? AND d.id=?',(item_id,action['document_id']),con)
                        if not doc:raise ValueError('Decision source must be evidence for this item')
                        con.execute('INSERT INTO decisions(item_id,field,value,document_id,document_hash,reason,created_at) VALUES(?,?,?,?,?,?,?) ON CONFLICT(item_id,field) DO UPDATE SET value=excluded.value,document_id=excluded.document_id,document_hash=excluded.document_hash,reason=excluded.reason,created_at=excluded.created_at',(item_id,action['field'],encode(action['value']),action['document_id'],doc['hash'],action['reason'],now()))
                    con.execute('UPDATE items SET version=version+1,updated_at=? WHERE id=?',(now(),item_id))
                changed.append(item_id)
            after={key:self._snapshot(con,int(key)) for key in before}
            receipt={'request_id':request_id,'state':'applied','item_ids':list(dict.fromkeys(changed)),'count':len(actions),'versions':{key:state['row']['version'] for key,state in after.items()}}
            con.execute('INSERT INTO action_receipts VALUES(?,?,?,?,?,?)',(request_id,body_hash,encode(receipt),encode(before),encode(after),now()))
            return receipt

    def undo(self,request_id,undo_id):
        request_id=request_uuid(request_id);undo_id=request_uuid(undo_id)
        body_hash=hashlib.sha256(encode({'undo':request_id}).encode()).hexdigest()
        with self.db.connection() as con:
            con.execute('BEGIN IMMEDIATE')
            retry=self._existing(con,undo_id,body_hash)
            if retry:return retry
            original=self.db.one('SELECT * FROM action_receipts WHERE request_id=?',(request_id,),con)
            if not original:raise ValueError('Unknown action receipt')
            before=json.loads(original['before_state']);after=json.loads(original['after_state'])
            con.execute('SAVEPOINT atomic_undo')
            snapshots={};results=[]
            for key,previous in before.items():
                item_id=int(key);current=self._snapshot(con,item_id);applied=after[key]
                applied.setdefault('reminder',{'present':False,'value':None})
                if previous is not None:previous.setdefault('reminder',{'present':False,'value':None})
                snapshots[key]=current
                restored=[];conflicts=[]
                if not current:
                    results.append({'item_id':item_id,'restored':[],'conflicts':['missing_item']});continue
                if previous is None:
                    if current==applied:
                        con.execute('UPDATE items SET deleted=1,version=version+1,updated_at=? WHERE id=?',(now(),item_id));restored=['created_item']
                    else:conflicts=['created_item_changed']
                else:
                    for field in ('status','deleted'):
                        if previous['row'][field]==applied['row'][field]:continue
                        if current['row'][field]==applied['row'][field]:
                            con.execute(f'UPDATE items SET {field}=? WHERE id=?',(previous['row'][field],item_id));restored.append(field)
                        else:conflicts.append(field)
                    old=json.loads(previous['row']['overrides']);new=json.loads(applied['row']['overrides']);live=json.loads(current['row']['overrides'])
                    missing=object()
                    for field in old.keys()|new.keys():
                        if old.get(field,missing)==new.get(field,missing):continue
                        if live.get(field,missing)==new.get(field,missing):
                            if field in old:live[field]=old[field]
                            else:live.pop(field,None)
                            restored.append(field)
                        else:conflicts.append(field)
                    con.execute('UPDATE items SET overrides=? WHERE id=?',(encode(live),item_id))
                    old_dec={d['field']:d for d in previous['decisions']};new_dec={d['field']:d for d in applied['decisions']};live_dec={d['field']:d for d in current['decisions']}
                    for field in old_dec.keys()|new_dec.keys():
                        if old_dec.get(field)==new_dec.get(field):continue
                        if live_dec.get(field)!=new_dec.get(field):conflicts.append('decision:'+field);continue
                        con.execute('DELETE FROM decisions WHERE item_id=? AND field=?',(item_id,field))
                        if field in old_dec:
                            d=old_dec[field];con.execute('INSERT INTO decisions VALUES(?,?,?,?,?,?,?,?)',tuple(d[k] for k in ('id','item_id','field','value','document_id','document_hash','reason','created_at')))
                        restored.append('decision:'+field)
                    if previous['reminder']!=applied['reminder']:
                        if current['reminder']==applied['reminder']:
                            write_personal_reminder(self.db,con,item_id,previous['reminder'])
                            restored.append('reminder')
                        else:conflicts.append('reminder')
                    if restored:con.execute('UPDATE items SET version=version+1,updated_at=? WHERE id=?',(now(),item_id))
                results.append({'item_id':item_id,'restored':restored,'conflicts':conflicts})
            blocked=any(result['conflicts'] for result in results)
            if blocked:
                con.execute('ROLLBACK TO atomic_undo')
                for result in results:result['restored']=[]
            con.execute('RELEASE atomic_undo')
            receipt={'request_id':undo_id,'undo_of':request_id,'state':'blocked' if blocked else 'undone','results':results}
            con.execute('INSERT INTO action_receipts VALUES(?,?,?,?,?,?)',(undo_id,body_hash,encode(receipt),encode(snapshots),encode({key:self._snapshot(con,int(key)) for key in snapshots}),now()))
            return receipt

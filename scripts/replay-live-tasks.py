#!/usr/bin/env python3
"""Replay recorded tasks from a Desktop Buddy state database through the current agent.

Each recorded step returns its recorded result (or raises its recorded error);
the model's recorded final reply is replayed as-is. Shows the outcome the
person would see now next to the outcome they saw then. Read-only on the source.

  python3 scripts/replay-live-tasks.py .buddy/state.sqlite3 [task-id-prefix …]
"""
import json
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from desktop_buddy.agent import Agent
from desktop_buddy.security import Policy,Redactor,StopToken
from desktop_buddy.state import ProviderConfig,Store
from desktop_buddy.tools import Registry


class Recorded:
    def __init__(self,script):
        self.config=ProviderConfig('lmstudio','http://127.0.0.1:1234/v1','chat',True,model='replay',tools=True,vision=True,context_tokens=400000)
        self.script=iter(script)
    def check(self,*_):pass
    def complete(self,*_):return next(self.script),{}


def load(source):
    work=Path(tempfile.mkdtemp())
    for suffix in ('','-wal','-shm'):
        if Path(str(source)+suffix).exists():shutil.copy(str(source)+suffix,work/('db'+suffix))
    return sqlite3.connect(work/'db'),work


def main():
    db,work=load(sys.argv[1]);wanted=sys.argv[2:]
    tasks=list(db.execute('select id,conversation,status from tasks order by rowid'))
    convs={c for t,c,s in tasks if s=='needs_review' and (not wanted or any(t.startswith(w) for w in wanted))}
    rows=[]
    for conversation in convs:
        root=Path(tempfile.mkdtemp());(root/'work').mkdir()
        store=Store(root/'state',Redactor());cid=store.conversation()
        for tid,_,was in [t for t in tasks if t[1]==conversation]:
            events=list(db.execute('select event,body,created from audit where task=? order by id',(tid,)))
            if was=='cancelled' or not events:continue
            start=events[0][2]
            last_user=next((json.loads(b) for b, in db.execute("select body from messages where conversation=? and created<=? order by created desc",(conversation,start))
                            if json.loads(b).get('role')=='user'),{})
            resume=str(last_user.get('content','')).startswith('Resume the previous interrupted task')
            steps=[];outcomes={};reply='(recorded reply)';host_retries=0
            for event,body,_ in events:
                body=json.loads(body)
                if event=='source_retry':host_retries=len(body['operations'])  # the host repeats these itself
                elif event=='tool_started' and host_retries:host_retries-=1
                elif event=='tool_started':
                    n=len(steps);steps.append({'role':'assistant','content':'','tool_calls':[{'id':str(n),'name':'run_tool',
                        'arguments':{'name':body['name'],'arguments':body.get('arguments',{}),'purpose':'Replay of a recorded step.'}}]})
                elif event=='tool_finished':outcomes.setdefault(body['name'],[]).append(('ok',body['result']))
                elif event=='tool_error':outcomes.setdefault(body['name'],[]).append(('error',body['error']))
                elif event=='unverified_model_report':reply=body['content']
            last={}
            registry=Registry(Policy(root/'work','yolo',False,lambda *_:True,protected=[store.root]),store)
            def execute(name,arguments,*_):
                # Results are matched per kind of step. A Continue task recorded before
                # the host retried failed reads has no recording for that retry: the
                # same request is given the same answer the model's own attempt got.
                queue=outcomes.get(name,[])
                kind,value=last[name]=queue.pop(0) if queue else last[name]
                if kind=='error':raise RuntimeError(value)
                return value
            registry.execute=execute
            Agent(store,registry,Recorded(steps+[{'role':'assistant','content':reply,'tool_calls':[]}])).run(cid,'Replay',resume=resume)
            now=store.tasks(cid)[0]['status'];message=store.messages(cid)[-1]['content']
            error=next((json.loads(r['body'])['error'] for r in store.audits() if r['event']=='task_error' and r['task']==store.tasks(cid)[0]['id']),None)
            if error:message='[replay error] '+error
            rows.append({'task':tid[:8],'then':was,'now':now,'resume':resume,'reply_kept':message.startswith(reply[:60]),
                         'note':message[len(reply):].strip()[:220] if message.startswith(reply[:60]) else message[:220]})
        store.close();shutil.rmtree(root)
    shutil.rmtree(work)
    print(json.dumps(rows,indent=1,ensure_ascii=False))


if __name__=='__main__':main()

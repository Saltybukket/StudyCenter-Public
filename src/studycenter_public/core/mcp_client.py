"""Public stdio bridge; looks up only the explicitly selected Public instance."""
import json
import sys
from pathlib import Path
from ..platform.paths import read_owned_identity
from ..platform.secrets import SecretStore


def main():
    import httpx
    sys.stdin.reconfigure(encoding='utf-8');sys.stdout.reconfigure(encoding='utf-8')
    data = Path(sys.argv[1]).resolve()
    client = sys.argv[2] if len(sys.argv) > 2 else 'codex'
    identity = read_owned_identity(data)
    if not identity: raise SystemExit('Public-Instanz fehlt.')
    raw = SecretStore(identity['instance_id']).get('mcp')
    token = json.loads(raw or '{}').get(client)
    if not token: raise SystemExit('Public MCP-Zugang fehlt.')
    runtime = json.loads((data / 'runtime.json').read_text(encoding='utf-8'))
    endpoint = runtime.get('url', '')
    if runtime.get('instance_id') != identity['instance_id'] or not endpoint.startswith('http://127.0.0.1:'):
        raise SystemExit('Public-Laufzeit nicht zugeordnet.')
    with httpx.Client(timeout=60, headers={'Authorization':'Bearer '+token, 'Accept':'application/json, text/event-stream'}, trust_env=False) as connection:
        for line in sys.stdin:
            try:
                message=json.loads(line)
            except json.JSONDecodeError:
                print(json.dumps({'jsonrpc':'2.0','id':None,'error':{'code':-32700,'message':'Ungültiges JSON.'}}),flush=True)
                continue
            try:
                response=connection.post(endpoint+'/mcp',json=message)
                response.raise_for_status()
                if response.status_code!=202: print(response.text,flush=True)
            except Exception as error:
                if isinstance(message,dict) and 'id' in message:
                    print(json.dumps({'jsonrpc':'2.0','id':message['id'],'error':{'code':-32603,'message':'CampusBoard nicht erreichbar / Anfrage abgewiesen: '+type(error).__name__}}),flush=True)

if __name__=='__main__': main()

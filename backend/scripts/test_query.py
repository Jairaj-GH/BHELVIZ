import os, sys, json
os.environ['BHELVIZ_JWT_SECRET']='test'
sys.path.append(r'C:\Users\rajor\Downloads\BHELVIZ_FULL\BHELVIZ_FULL\backend')

import dev_main
import BHELVIZ_FULL.backend.core.database as database
import BHELVIZ_FULL.backend.core.auth as auth
from fastapi.testclient import TestClient

# Use dev DB instead of Oracle for this test
dev_main.get_oracle_session = database.get_dev_session

# Create admin token
token = auth.create_access_token(user_id=1, email='admin@bhel.in', role='admin')

client = TestClient(dev_main.app)

payload = {"utterance": "show absentees today in HR", "session_id": "test_sess", "history": []}
resp = client.post('/query', json=payload, headers={'Authorization': f'Bearer {token}'})
print('STATUS', resp.status_code)
try:
    print('JSON', json.dumps(resp.json(), indent=2))
except Exception as e:
    print('RESPONSE TEXT', resp.text)
    print('ERROR', e)

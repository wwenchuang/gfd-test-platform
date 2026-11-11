"""Publish a review under the run owner's identity; receive the personal session on stdin."""
import argparse
import getpass
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from task_server import auth
from task_server.services import workbench_triage_model as model
from scripts.week12_triage.fixtures import evaluate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--evaluate-fixtures', action='store_true')
    args = parser.parse_args()
    try:
        token = getpass.getpass('Platform session: ') if sys.stdin.isatty() else sys.stdin.readline(512).strip()
        session = auth.verify_session_token(token)
        if not session:
            raise PermissionError()
        actor = session['user_id'], session['user'], token
        result = model.generate(actor,args.run_id)
        print(json.dumps({k:result[k] for k in ('run_id','status','category','requested_model','model','usage','duration_ms','provider_calls')}, ensure_ascii=False))
        if args.evaluate_fixtures:
            rows = evaluate()
            model.publish_evaluation(actor,rows)
            print(json.dumps(model.read_evaluation(actor),ensure_ascii=False))
            if not all(row['verified'] for row in rows):
                return 1
        return 0 if result['status'] == 'ready' else 2
    except Exception:
        print('Review unavailable. Check the personal session, run ownership, dependencies and model configuration.',file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())

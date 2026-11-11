"""Compare both retrievers against the same frozen, independently labelled questions."""
import argparse
from collections import Counter
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import sys
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
FIXTURES = ROOT / 'tests/fixtures/practice_knowledge_eval'
METHODS = ('keyword', 'vector')
APP = 'com.kfb.model'


def digest(path):
    return sha256(path.read_bytes()).hexdigest()


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def reference(hit):
    return (hit['document_id'], hit['paragraph_id'])


def read_questions():
    splits = {name: read_json(FIXTURES / f'{name}.json') for name in ('tuning', 'holdout')}
    seen_ids, seen_questions = set(), set()
    for name, rows in splits.items():
        counts = Counter(row['kind'] for row in rows)
        expected = (20, 5, 5) if name == 'holdout' else (6, 2, 2)
        if counts != dict(zip(('answerable', 'unknown', 'forbidden'), expected)):
            raise ValueError(f'{name}: unexpected question distribution {counts}')
        for row in rows:
            q = ''.join(row['question'].split()).casefold()
            if q in seen_questions or row['id'] in seen_ids:
                raise ValueError('Repeated question or ID across splits')
            seen_ids.add(row['id'])
            seen_questions.add(q)
            if (row['kind'] == 'forbidden') == (APP in row['allowed_apps']):
                raise ValueError(f"{row['id']}: inconsistent access label")
            if row['kind'] == 'answerable' and (len(row['gold']) != 1 or not row['required_terms']):
                raise ValueError('Each answerable item requires one primary evidence paragraph and a factual answer key')
            if row['kind'] != 'answerable' and (row['gold'] or row['required_terms']):
                raise ValueError('Unknown and forbidden responses must abstain')
    return splits


def elapsed_ms(start):
    return round((perf_counter() - start) * 1000, 3)


def score(row, result, paragraphs, unknown_answer):
    hits = result['results']
    top5 = [{'document_id': h['document_id'], 'paragraph_id': h['paragraph_id']} for h in hits[:5]]
    gold = {reference(g) for g in row['gold']}
    answer = result['answer']
    status = result['status']
    refused = status == 'unknown' and answer == unknown_answer and not hits
    # Inspect the whole actual result for denied queries, including metadata counts.
    leaked = False
    if row['kind'] == 'forbidden':
        leaked = bool(hits or answer != unknown_answer or result['document_count'] or result['paragraph_count'])
        if not leaked:
            serialized = json.dumps(result, ensure_ascii=False)
            leaked = any(doc_id in serialized for doc_id, _ in paragraphs)
    hits_gold = sum(reference(h) in gold for h in top5)
    supported = False
    if hits and gold and status == 'cited':
        primary = hits[0]
        key = reference(primary)
        excerpt = primary['excerpt']
        # The answer itself must contain the cited evidence plus every labelled fact.
        supported = (key in gold and bool(excerpt) and excerpt in paragraphs[key]
                     and excerpt in answer and all(any(term in answer for term in options)
                                                    for options in row['required_terms']))
    return dict(status=status, top5=[] if leaked else top5, recall_hits=min(hits_gold, len(gold)),
                citation_supported=supported, refused=refused, leaked=leaked, error=None)


def summarize(rows, method, duration):
    counts = Counter()
    totals = Counter()
    errors = 0
    for row in rows:
        outcome = row['results'][method]
        errors += int(outcome['error'] is not None)
        if row['kind'] == 'answerable':
            scores = {'recall_at_5': bool(outcome['recall_hits']), 'citation_support': outcome['citation_supported']}
        elif row['kind'] == 'forbidden':
            scores = {'access_refusal': outcome['refused'], 'zero_leak': outcome['error'] is None and not outcome['leaked']}
        else:
            scores = {'unknown_refusal': outcome['refused']}
        for metric, passed in scores.items():
            counts[metric] += int(passed)
            totals[metric] += 1
    return {'metrics': {metric: {'count': counts[metric], 'total': total} for metric, total in totals.items()},
            'errors': errors, 'elapsed_ms': round(duration, 3)}


def load_sources():
    docs = {}
    paragraphs = {}
    for path in sorted((ROOT / 'task_server/knowledge_sources').glob('*.json')):
        doc = read_json(path)
        if doc['app_id'] != APP:
            continue
        doc_id = doc['document_id']
        docs[doc_id] = {'snapshot_sha256': digest(path), 'source_sha256': doc['source_sha256'],
                        'version': doc['version'], 'paragraph_count': len(doc['paragraphs'])}
        for p in doc['paragraphs']:
            paragraphs[(doc_id, p['paragraph_id'])] = p['excerpt']
    return docs, paragraphs


def verify_keys(rows, paragraphs):
    for row in rows:
        for key in row['gold']:
            passage = paragraphs.get(reference(key), '')
            for options in row['required_terms']:
                if not any(term in passage for term in options):
                    raise ValueError(f"{row['id']} answer key is unsupported by its frozen primary source")


def evaluate(split):
    from task_server.services import practice_knowledge_search as engine

    rows = read_questions()[split]
    sources, paragraphs = load_sources()
    verify_keys(rows, paragraphs)
    start = perf_counter()
    durations = Counter()
    measured = []
    for i, row in enumerate(rows):
        results = {}
        # Alternate query order to distribute cold/warm filesystem effects.
        for method in METHODS[::1 if i % 2 == 0 else -1]:
            begin = perf_counter()
            try:
                actual = engine.search(row['question'], allowed_apps=row['allowed_apps'], method=method)
                duration = elapsed_ms(begin)
            except Exception as exc:
                duration = elapsed_ms(begin)
                # Preserve the exception class without serializing paths or private source data.
                outcome = dict(status='error', top5=[], recall_hits=0, citation_supported=False,
                               refused=False, leaked=False, error=type(exc).__name__)
            else:
                outcome = score(row, actual, paragraphs, engine.UNKNOWN)
            results[method] = {**outcome, 'elapsed_ms': duration}
            durations[method] += duration
        measured.append({**row, 'results': results})
    return {
        'schema_version': 1, 'split': split, 'scope': {'app_ids': [APP]},
        'created_at': datetime.now(timezone.utc).isoformat(),
        'runtime': {'python': sys.version.split()[0], 'search_sha256': digest(Path(engine.__file__)),
                    'scorer_sha256': digest(Path(__file__)), 'elapsed_ms': elapsed_ms(start)},
        'fixture_sha256': digest(FIXTURES / f'{split}.json'), 'sources': sources,
        'methods': {method: {'engine': engine.ENGINES[method], **summarize(measured, method, durations[method])}
                    for method in METHODS}, 'questions': measured,
        'human_review': {'reviewed': 0, 'total': len(rows), 'status': 'pending'},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--split', choices=['tuning', 'holdout'], required=True)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    report = evaluate(args.split)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report['methods'], ensure_ascii=False, indent=2))
    return int(any(m['errors'] for m in report['methods'].values()))


if __name__ == '__main__':
    sys.exit(main())

"""Offline extractive QA over the currently authorized source snapshots.

The keyword baseline uses BM25. The vector index projects TF-IDF into an
explicit concept space (ESA) with one dimension per authorized passage.
No text leaves the process and there is no persistent index to revoke.
"""

import json
import math
import re
import unicodedata
from collections import Counter
from pathlib import Path


SOURCE_DIR = Path(__file__).resolve().parents[1] / 'knowledge_sources'
ENGINES = {'keyword': 'BM25', 'vector': '本地段落概念向量（ESA / 余弦）'}
UNKNOWN = '当前可见资料没有足够证据。'
QUESTION_FILLER = re.compile(
    r'请问|请告诉我|想知道|如何|怎么|什么|哪些|怎样|是否|为什么|多少|能不能|有没有|应该|需要吗|的是|的|了|吗|呢|？'
)


def _terms(text):
    text = unicodedata.normalize('NFKC', text).lower()
    terms = []
    for token in re.findall(r'[\u3400-\u9fff]+|[a-z0-9]+', text):
        if not '\u3400' <= token[0] <= '\u9fff' or len(token) == 1:
            terms.append(token)
        else:
            terms.extend(token[i:i + 2] for i in range(len(token) - 1))
    return Counter(terms)


def _passages(allowed_apps):
    passages = []
    doc_ids = set()
    for source in sorted(SOURCE_DIR.glob('*.json')):
        try:
            doc = json.loads(source.read_text(encoding='utf-8'))
        except FileNotFoundError:
            continue  # Withdrawn between listing and opening the snapshot.
        if doc['app_id'] not in allowed_apps:
            continue
        doc_ids.add(doc['document_id'])
        fields = {k: doc[k] for k in ('document_id', 'title', 'kind', 'version')}
        passages.extend({**fields, **p} for p in doc['paragraphs'])
    return passages, len(doc_ids)


def _unit(vector):
    norm = math.sqrt(sum(value * value for value in vector.values()))
    return {term: value / norm for term, value in vector.items()} if norm else {}


def _dot(a, b):
    if len(a) > len(b):
        a, b = b, a
    return sum(value * b.get(term, 0) for term, value in a.items())


def _concept_scores(query, counts, idf):
    def tfidf(words):
        return _unit({w: (1 + math.log(n)) * idf[w] for w, n in words.items() if w in idf})

    basis = [tfidf(row) for row in counts]
    q = tfidf(query)

    def project(vector):
        return _unit({i: _dot(vector, row) for i, row in enumerate(basis)})

    projected_query = project(q)
    return [_dot(projected_query, project(row)) for row in basis]


def _keyword_scores(query, counts, idf):
    avg_len = sum(sum(row.values()) for row in counts) / len(counts)
    scores = []
    for row in counts:
        length_factor = 1.2 * (0.25 + 0.75 * sum(row.values()) / max(1, avg_len))
        scores.append(sum(idf[w] * 2.2 * row[w] / (row[w] + length_factor)
                          for w in query.keys() & row.keys()))
    return scores


def search(question, *, allowed_apps, method='vector'):
    """Return zero to five cited passages. The caller supplies resolved app IDs."""
    if not isinstance(question, str) or not 1 <= len(question.strip()) <= 500:
        raise ValueError('请输入 1 至 500 字的问题')
    if method not in ENGINES:
        raise ValueError('未知的检索方法')
    if isinstance(allowed_apps, str) or allowed_apps is None:
        raise ValueError('需要明确的应用权限集合')
    passages, doc_count = _passages(set(allowed_apps))
    result = dict(method=method, engine=ENGINES[method], status='unknown', answer=UNKNOWN,
                  results=[], document_count=doc_count, paragraph_count=len(passages))
    query = _terms(QUESTION_FILLER.sub(' ', question))
    if not passages or not query:
        return result
    # Use excerpt and title as the retrievable text. Keep citation text verbatim.
    counts = [_terms(p['excerpt'] + ' ' + Path(p['title']).stem) for p in passages]
    df = Counter(w for row in counts for w in row)
    idf = {w: math.log1p((len(passages) - n + 0.5) / (n + 0.5)) for w, n in df.items()}
    score_fn = _keyword_scores if method == 'keyword' else _concept_scores
    scores = score_fn(query, counts, idf)
    # Topic overlap alone is insufficient. Require the query's specific terms
    # to be present within the same source passage before offering an answer.
    ranked = sorted(range(len(passages)), key=lambda i: -scores[i])
    def supported(i):
        overlap = query.keys() & counts[i].keys()
        coverage = len(overlap) / len(query)
        if coverage >= 0.44:
            return True
        # Distinctive overlapping terms in body text can carry a longer
        # paraphrase. A match to the source filename alone cannot do so.
        body_overlap = query.keys() & _terms(passages[i]['excerpt']).keys()
        rare = sum(idf[word] >= 2.5 for word in body_overlap)
        return coverage >= 0.25 and rare >= 2

    eligible = [i for i in ranked if scores[i] > 0 and supported(i)]
    if eligible:
        hits = [{**passages[i], 'score': round(scores[i], 6)} for i in eligible[:5]]
        result.update(status='cited', answer=hits[0]['excerpt'], results=hits)
    return result

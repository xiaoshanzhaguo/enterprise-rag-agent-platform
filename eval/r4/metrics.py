"""Evidence retrieval metrics. No model, database or production imports."""
import math


def unique_results(results):
    """Deduplicate stable Chunk keys in first-occurrence order, before applying K."""
    seen, unique = set(), []
    for result in results:
        key = result['chunk_key']
        if key not in seen:
            seen.add(key)
            unique.append(result)
    return unique


def evaluate_ranking(results, qrels, relevant_documents, k):
    """All positive grades are relevant; invalid versions retain a rank but receive gain zero."""
    if type(k) is not int or k not in (1, 3, 5):
        raise ValueError('K must be 1, 3 or 5')
    if any(type(grade) is not int or grade not in (0, 1, 2) for grade in qrels.values()):
        raise ValueError('Relevance grade must be 0, 1 or 2')
    ranked = unique_results(results)[:k]
    positive = {key for key, grade in qrels.items() if grade > 0}
    documents = set(relevant_documents)
    valid = [row for row in ranked if row.get('eligible', True)]
    hits = {row['chunk_key'] for row in valid} & positive
    doc_hits = {row['document_key'] for row in valid} & documents
    grades = [qrels.get(row['chunk_key'], 0) if row.get('eligible', True) else 0 for row in ranked]
    ideal = sorted((qrels[key] for key in positive), reverse=True)[:k]
    dcg = sum((2 ** grade - 1) / math.log2(rank + 2) for rank, grade in enumerate(grades))
    idcg = sum((2 ** grade - 1) / math.log2(rank + 2) for rank, grade in enumerate(ideal))
    first = next((1 / rank for rank, grade in enumerate(grades, 1) if grade > 0), 0.0)
    return {
        'k': k, 'document_recall': len(doc_hits) / len(documents) if documents else None,
        'chunk_recall': len(hits) / len(positive) if positive else None,
        'mrr_at_k': first if positive else None, 'ndcg_at_k': dcg / idcg if idcg else None,
        'dcg': dcg, 'ideal_dcg': idcg, 'ranked_grades': grades,
        'relevant_chunk_count': len(positive), 'hit_chunk_keys': sorted(hits),
        'missing_chunk_keys': sorted(positive - hits), 'missing_document_keys': sorted(documents - doc_hits),
        'raw_result_count': len(results), 'unique_result_count': len(unique_results(results)),
        'version_filter_violations': [row['chunk_key'] for row in ranked if not row.get('eligible', True)],
    }


def macro_average(scores):
    summary = {'question_count': len(scores)}
    for metric in ('document_recall', 'chunk_recall', 'mrr_at_k', 'ndcg_at_k'):
        values = [score[metric] for score in scores if score[metric] is not None]
        summary[metric] = {'mean': sum(values) / len(values) if values else None, 'evaluated_questions': len(values)}
    return summary

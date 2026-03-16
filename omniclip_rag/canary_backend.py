from __future__ import annotations

from collections.abc import Iterable


CANARY_VECTOR_MODEL_ID = '__omniclip_canary_torch__'
CANARY_RERANKER_MODEL_ID = '__omniclip_canary_torch_reranker__'

_FEATURE_GROUPS: tuple[tuple[str, ...], ...] = (
    ('思维', '思考', '想法', 'mind', 'thinking'),
    ('框架', '模型', '方法', 'framework', 'model'),
    ('问题', '拆解', '分析', 'problem', 'analysis'),
    ('任务', '清单', 'todo', 'shopping', '购物'),
    ('笔记', '记录', 'logseq', 'note'),
)


def is_canary_vector_model(model_name: str | None) -> bool:
    return str(model_name or '').strip() == CANARY_VECTOR_MODEL_ID


def is_canary_reranker_model(model_name: str | None) -> bool:
    return str(model_name or '').strip() == CANARY_RERANKER_MODEL_ID


def encode_text_tensor(torch_module, text: str, *, device: str):
    normalized = str(text or '').strip().lower()
    feature_values = [
        float(sum(normalized.count(token) for token in group))
        for group in _FEATURE_GROUPS
    ]
    feature_values.extend(
        (
            float(len(normalized)),
            float(sum(normalized.count(char) for char in '我的思维框架问题拆解记录')),
            float(1 if '思维' in normalized or '思考' in normalized else 0),
            float(1 if '框架' in normalized or '模型' in normalized else 0),
        )
    )
    return torch_module.tensor(feature_values, dtype=torch_module.float32, device=device)


def encode_batch_tensors(torch_module, texts: Iterable[str], *, device: str, normalize: bool = True):
    rows = [encode_text_tensor(torch_module, text, device=device) for text in texts]
    if not rows:
        return torch_module.empty((0, 0), dtype=torch_module.float32, device=device)
    batch = torch_module.stack(rows)
    if normalize:
        norms = torch_module.linalg.vector_norm(batch, dim=1, keepdim=True)
        batch = batch / torch_module.clamp(norms, min=1e-6)
    return batch


def rerank_score_tensor(torch_module, query_text: str, candidate_text: str, *, device: str):
    query_vector = encode_text_tensor(torch_module, query_text, device=device)
    candidate_vector = encode_text_tensor(torch_module, candidate_text, device=device)
    query_vector = query_vector / torch_module.clamp(torch_module.linalg.vector_norm(query_vector), min=1e-6)
    candidate_vector = candidate_vector / torch_module.clamp(torch_module.linalg.vector_norm(candidate_vector), min=1e-6)
    semantic_score = torch_module.dot(query_vector, candidate_vector)
    length_bonus = torch_module.tensor(
        min(len(str(candidate_text or '').strip()) / 120.0, 1.0),
        dtype=torch_module.float32,
        device=device,
    )
    return semantic_score * 0.92 + length_bonus * 0.08

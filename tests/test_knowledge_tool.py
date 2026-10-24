"""离线验证知识库 Tool、检索兜底及 Agent 接入；不写数据库或调用模型。"""

import unittest
from unittest.mock import patch

from pydantic import ValidationError

from backend.rag.service import NO_RAG_EVIDENCE_MESSAGE
from backend.rag.tools import create_knowledge_search_tool
from backend.services.agent_service import _retrieve_agent_rag_chunks


CHUNK = {
    "chunk_id": 2, "file_name": "员工手册.md", "text": "年假需要提前申请。",
    "score": 0.9, "knowledge_base_type": "hr",
}


class KnowledgeToolTests(unittest.TestCase):
    def setUp(self):
        self.tool = create_knowledge_search_tool(
            session_id="session-a", knowledge_base_id="kb-a",
            knowledge_base_type_filter="hr", top_k=2,
        )

    @patch("backend.rag.tools.retrieve_rag_chunks_with_mode")
    def test_scoped_search_preserves_metadata(self, retrieve):
        retrieve.return_value = ([CHUNK], "vector")
        result = self.tool.invoke({"query": "  年假申请  "})
        retrieve.assert_called_once_with(
            session_id="session-a", query="年假申请", knowledge_base_id="kb-a",
            knowledge_base_type_filter="hr", top_k=2,
        )
        self.assertEqual(result["chunks"], [CHUNK])
        self.assertEqual(result["retrieval_mode"], "vector")
        self.assertIn("员工手册.md#chunk-2", result["context"])
        self.assertEqual(set(self.tool.args), {"query"})

    @patch("backend.rag.tools.retrieve_rag_chunks_with_mode")
    def test_invalid_input_does_not_search(self, retrieve):
        for payload in ({"query": " "}, {}, {"query": "制度", "knowledge_base_id": "other"}):
            with self.subTest(payload=payload), self.assertRaises(ValidationError):
                self.tool.invoke(payload)
        retrieve.assert_not_called()

    def test_top_k_must_be_positive_integer(self):
        for value in (0, -1, True, 1.5):
            with self.subTest(value=value), self.assertRaises(ValueError):
                create_knowledge_search_tool(session_id="s", top_k=value)

    @patch("backend.rag.service.settings")
    @patch("backend.rag.service.retrieve_keyword_chunks")
    @patch("backend.rag.service.retrieve_similar_chunks")
    def test_vector_and_keyword_fallback(self, vector, keyword, settings):
        settings.rag_retrieval_mode = "vector"
        settings.rag_keyword_fallback_enabled = True
        vector.return_value = [CHUNK]
        result = self.tool.invoke({"query": "年假"})
        self.assertEqual(result["retrieval_mode"], "vector")
        self.assertEqual(result["chunks"][0]["rank"], 1)
        vector.assert_called_once_with(
            session_id="session-a", query="年假", knowledge_base_id="kb-a",
            knowledge_base_type_filter="hr", top_k=2,
        )
        keyword.assert_not_called()
        vector.return_value = []
        keyword.return_value = [dict(CHUNK, retrieval_mode="keyword", rank=1)]
        self.assertEqual(self.tool.invoke({"query": "年假"})["retrieval_mode"], "keyword")
        settings.rag_keyword_fallback_enabled = False
        keyword.reset_mock()
        result = self.tool.invoke({"query": "年假"})
        self.assertEqual(result["chunks"], [])
        self.assertEqual(result["retrieval_mode"], "no_hit")
        self.assertIn(NO_RAG_EVIDENCE_MESSAGE, result["context"])
        keyword.assert_not_called()

    @patch("backend.rag.service.retrieve_similar_chunks")
    def test_no_scope_has_no_hits(self, vector):
        tool = create_knowledge_search_tool(session_id=None)
        self.assertEqual(tool.invoke({"query": "年假"})["retrieval_mode"], "no_hit")
        vector.assert_not_called()

    @patch("backend.rag.tools.retrieve_rag_chunks_with_mode")
    def test_agent_retries_original_query_through_tool(self, retrieve):
        retrieve.side_effect = [([], "no_hit"), ([CHUNK], "keyword")]
        chunks, mode, query, note = _retrieve_agent_rag_chunks(
            "s", "改写问题", "原问题", "kb", "hr", 3,
        )
        self.assertEqual((chunks, mode, query), ([CHUNK], "keyword", "原问题"))
        self.assertIn("原始问题", note)
        self.assertEqual([c.kwargs["query"] for c in retrieve.call_args_list], ["改写问题", "原问题"])

    @patch("backend.rag.tools.retrieve_rag_chunks_with_mode")
    def test_agent_primary_hit_and_no_duplicate_retry(self, retrieve):
        retrieve.return_value = ([CHUNK], "vector")
        self.assertEqual(_retrieve_agent_rag_chunks("s", "年假", "原问题", None, None, 3)[1], "vector")
        retrieve.assert_called_once()
        retrieve.reset_mock()
        retrieve.return_value = ([], "no_hit")
        self.assertEqual(_retrieve_agent_rag_chunks("s", "年假", "年假", None, None, 3)[0], [])
        retrieve.assert_called_once()
        retrieve.reset_mock()
        self.assertEqual(_retrieve_agent_rag_chunks("s", " ", " ", None, None, 3)[2], "")
        retrieve.assert_not_called()


if __name__ == "__main__":
    unittest.main()

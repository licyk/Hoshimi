"""TypeSafe System One classification, kept outside the chat model catalog."""

from __future__ import annotations

import math
from collections.abc import Iterator
from typing import Any

from hoshimi.core.errors import ValidationError
from hoshimi.core.providers.base import Account, ProviderError
from hoshimi.core.providers.keyed import KeyedProvider
from hoshimi.core.providers.openai_compat import raise_for_status
from hoshimi.core.quota import observe_response


def _number(value: Any) -> bool:
    return isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value)


class TypeSafeProvider(KeyedProvider):
    id = "typesafe"
    display_name = "TypeSafe"
    base_url = "https://api.typesafe.ai/v1"

    def models(self) -> list[str]:
        return []

    def classifier_models(self) -> list[str]:
        return list(dict.fromkeys(str(model) for account in self.accounts for model in account.get("classifiers", self.options.get("classifiers", ["jev-latest"]))))

    def stream_raw(self, account: Account, model: str, body: dict[str, Any]) -> Iterator[dict[str, Any]]:
        if not body.get("_classification"):
            raise ValidationError("TypeSafe is a classifier. Use /v1/classify.")
        questions = body.get("questions")
        if not isinstance(body.get("state"), dict) or not isinstance(questions, dict) or not questions:
            raise ValidationError("Classification requires state and a nonempty questions object.")
        if body.get("images"):
            raise ValidationError("TypeSafe System One does not support image input.")
        wire_questions: dict[str, Any] = {}
        for key, question in questions.items():
            if not isinstance(question, dict) or question.get("type") not in ("bool", "score", "choice"):
                raise ValidationError("Question types must be bool, score or choice.")
            wire_questions[key] = {**question, "type": "noul" if question["type"] == "bool" else question["type"]}
        response = self.client.post(f"{self.base_url}/systemone", headers=self.build_headers(account, model),
                                    json={"model": model, "state": body["state"], "questions": wire_questions}, timeout=60)
        observe_response(self.id, response)
        raise_for_status(response, provider=self.id)
        payload = response.json()
        answers = payload.get("answers", {})
        result: dict[str, Any] = {}
        for key, question in questions.items():
            answer = answers.get(key)
            if not isinstance(answer, dict):
                raise ProviderError(502, f"typesafe: no answer for {key}")
            kind = question["type"]
            if kind == "bool":
                probability = answer.get("noul")
                if answer.get("type") != "noul" or not _number(probability) or not 0 <= probability <= 1:
                    raise ProviderError(502, "typesafe: invalid boolean probability")
                result[key] = {"type": "bool", "probability": probability}
            else:
                if answer.get("type") != kind or not _number(answer.get("confidence")):
                    raise ProviderError(502, "typesafe: invalid answer type or confidence")
                if kind == "score" and not _number(answer.get("score")):
                    raise ProviderError(502, "typesafe: invalid score")
                if kind == "choice" and (not isinstance(answer.get("choice"), str) or not isinstance(answer.get("probabilities"), dict) or not all(_number(p) for p in answer["probabilities"].values())):
                    raise ProviderError(502, "typesafe: invalid choice probabilities")
                result[key] = answer
        usage = payload.get("usage")
        yield {"type": "classification", "result": {"provider": self.id, "model": model, "answers": result, "usage": usage}}
        if isinstance(usage, dict):
            prompt = usage.get("input_tokens", usage.get("prompt_tokens", 0))
            output = usage.get("output_tokens", usage.get("completion_tokens", 0))
            yield {"type": "usage", "usage": {"prompt_tokens": prompt, "completion_tokens": output, "total_tokens": prompt + output}}
        yield {"type": "finish", "reason": "stop"}

import os
from datetime import datetime

from openai import OpenAI


SYSTEM_PROMPT = (
    "你是一位高级 IT 项目经理，擅长从会议转录中提炼关键信息并形成可执行的结论。"
)


def build_prompt(transcript_text: str) -> str:
    return f"""请从以下会议文字记录中生成结构化会议纪要，要求输出 Markdown：

1. Executive Summary（高管摘要，3~6 条要点）
2. Key Discussion Points（关键讨论点，分主题列点）
3. Action Items with Assignees（待办事项，必须包含负责人/Owner；若原文未明确负责人请标注“Owner: TBD”）

会议转录如下：
---
{transcript_text}
---
"""


class LLMSummarizer:
    def __init__(self, llm_cfg: dict) -> None:
        self.llm_cfg = llm_cfg
        self._client = OpenAI(
            api_key=llm_cfg.get("api_key", ""),
            base_url=llm_cfg.get("base_url", ""),
        )
        self._model = llm_cfg.get("model", "")

    def generate_summary(self, log_file_path: str) -> str:
        with open(log_file_path, "r", encoding="utf-8") as f:
            transcript = f.read()

        prompt = build_prompt(transcript)
        resp = self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
        )
        content = resp.choices[0].message.content or ""

        # 输出文件名与日志一致（同时间戳）
        base = os.path.splitext(os.path.basename(log_file_path))[0]
        out_name = f"Meeting_Summary_{base.replace('meeting_log_', '')}.md"
        out_path = os.path.join(os.path.dirname(log_file_path) or ".", out_name)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(content)

        return out_path


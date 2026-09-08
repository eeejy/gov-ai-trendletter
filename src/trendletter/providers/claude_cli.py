"""Claude Code CLI. 별도 키 없이 이미 쓰는 구독을 그대로 쓴다."""

from __future__ import annotations

import shutil
import subprocess
from typing import List, Optional

from .base import Provider, ProviderError


class ClaudeCli(Provider):
    def command(self) -> str:
        return str(self.opts.get("command") or self.cfg.get("llm.command", "claude"))

    def available(self) -> bool:
        return shutil.which(self.command()) is not None

    def why_not(self) -> str:
        return ("%s 명령을 찾을 수 없습니다. Claude Code 를 설치하거나 "
                "다른 제공자를 고르세요." % self.command())

    def generate(self, prompt: str, timeout: Optional[int] = None) -> str:
        exe = shutil.which(self.command())
        if not exe:
            raise ProviderError(self.why_not())

        args = [exe, "-p", "--output-format", "text"]
        model = self.opts.get("model")
        if model:
            args += ["--model", str(model)]

        try:
            # 프롬프트를 인자가 아니라 표준입력으로 넘긴다.
            # 재순위 프롬프트가 40KB 라 Windows 명령줄 한계(32KB)를 넘었다.
            proc = subprocess.run(args, input=prompt, capture_output=True,
                                  text=True, timeout=self.timeout(timeout))
        except subprocess.TimeoutExpired as exc:
            raise ProviderError("모델 응답이 시간 안에 오지 않았습니다") from exc

        if proc.returncode != 0:
            raise ProviderError("claude 실행 실패(코드 %d): %s"
                                % (proc.returncode, (proc.stderr or "")[:300]))
        return proc.stdout

    def models(self) -> List[str]:
        return ["", "opus", "sonnet", "haiku"]     # 빈 값은 CLI 기본

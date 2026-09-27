"""
title: Perplexity Sonar Manifold Pipe.

author: yazon
author_url: https://github.com/open-webui/open-webui/discussions/11212
funding_url: https://github.com/open-webui
version: 0.2.2
"""

import json
import logging
from collections.abc import AsyncGenerator, Awaitable, Callable

import httpx
from pydantic import BaseModel, Field

from utils.misc import pop_system_message


class PipeExceptionError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)


class Pipe:
    class Valves(BaseModel):
        """
        Configuration settings for Perplexity API pipe.

        Attributes:
            PERPLEXITY_API_BASE_URL: Perplexity API base URL. Defaults to official API URL.
            PERPLEXITY_API_KEY: Authentication key for Perplexity API. Required.
            RETURN_IMAGES: Whether responses should include images. Default False.
            EMIT_SOURCES: Whether to include citation sources in responses. Default True.
            SEARCH_RECENCY_FILTER: Time filter for search results (month/week/day/hour/none).
            SEARCH_CONTEXT_SIZE: Amount of search context to retrieve (low/medium/high/none).
        """

        PERPLEXITY_API_BASE_URL: str = Field(
            default="https://api.perplexity.ai",
            description="Perplexity API base URL.",
        )
        PERPLEXITY_API_KEY: str = Field(
            default="",
            description="Perplexity API key. Required — set via environment variable PERPLEXITY_API_KEY.",
        )
        RETURN_IMAGES: bool = Field(
            default=False,
            description="Determines whether or not a request to an online model should return images. It requires higher tier otherwise error is returned.",
        )
        EMIT_SOURCES: bool = Field(
            default=True,
            description="Emit sources.",
        )
        SEARCH_RECENCY_FILTER: str = Field(
            default="none",
            available_values=["month", "week", "day", "hour", "none"],
            description="Returns search results within the specified time interval - does not apply to images. Values include month, week, day, hour.",
        )
        SEARCH_CONTEXT_SIZE: str = Field(
            default="none",
            available_values=["low", "medium", "high"],
            description="Determines how much search context is retrieved for the model. Values allowed: low, medium, high.",
        )

    def __init__(self) -> None:
        self.type = "manifold"
        self.valves = self.Valves()
        self.timeout = 60

    def pipes(self) -> list[dict[str, str]]:
        """Return a list of available pipes with their IDs and names."""
        return [
            {"id": "sonar", "name": "Sonar"},
            {"id": "sonar-pro", "name": "Sonar Pro"},
            {"id": "sonar-reasoning", "name": "Sonar Reasoning"},
            {"id": "sonar-reasoning-pro", "name": "Sonar Reasoning Pro"},
            {"id": "sonar-deep-research", "name": "Sonar Deep Research"},
            {"id": "r1-1776", "name": "R1-1776 Offline"},
        ]

    def _format_citations_as_sources(self, citations: list) -> list:
        """
        Format citations to match the expected structure for sources.

        Citations are always a list of URLs that need to be formatted properly
        for display in the UI.
        Expected format comes from Chat.svelte.
        """
        formatted_sources = []

        if isinstance(citations, list):
            for i, citation in enumerate(citations):
                if isinstance(citation, str):
                    formatted_sources.append({
                        "source": {
                            "name": f"[{i + 1}]",
                            "type": "web_search_results",
                            "urls": [citation],
                        },
                        "document": ["Click the link to view the content."],
                        "metadata": [{"source": citation}],
                    })

        return formatted_sources

    async def _emit_sources(self, citations: list[str], event_emitter: Callable[[dict], Awaitable[None]]) -> None:
        """
        Emit sources if the valve is enabled.

        Args:
            citations: A list of citation URLs.
            event_emitter: A callable to emit events.
        """
        if isinstance(citations, list) and self.valves.EMIT_SOURCES:
            sources = self._format_citations_as_sources(citations)
            if sources and event_emitter:
                await event_emitter({"type": "chat:completion", "data": {"sources": sources}})

    def _process_line(self, line: str) -> tuple[str, list[str]]:
        """
        Process a line of input to extract citations.

        Args:
            line: The input line to process.

        Returns:
            A tuple containing the original line and a list of citations.
        """
        citations = []
        if line and line.startswith("data:"):
            # Extract citations from the data
            json_start = line.find("{")
            if json_start != -1:
                line_copy = json.loads(line[json_start:])
                citations = line_copy.get("citations", [])
        return line, citations

    async def pipe(
        self, body: dict, __event_emitter__: Callable[[dict], Awaitable[None]] | None = None
    ) -> AsyncGenerator[str | dict, None]:
        if not self.valves.PERPLEXITY_API_KEY:
            msg = "PERPLEXITY_API_KEY not provided in the valves."
            raise PipeExceptionError(msg)

        headers = {"Authorization": f"Bearer {self.valves.PERPLEXITY_API_KEY}", "Content-Type": "application/json"}

        system_message, messages = pop_system_message(body.get("messages", []))
        system_prompt = "You are a helpful assistant."
        if system_message is not None:
            system_prompt = system_message["content"]

        model_id = body["model"].split(".")[-1]

        payload = {
            "model": model_id,
            "messages": [{"role": "system", "content": system_prompt}, *messages],
            "stream": body.get("stream", True),
            "return_images": self.valves.RETURN_IMAGES,
        }
        if self.valves.SEARCH_RECENCY_FILTER != "none":
            payload["search_recency_filter"] = self.valves.SEARCH_RECENCY_FILTER
        if self.valves.SEARCH_CONTEXT_SIZE != "none":
            payload["web_search_options"] = {"search_context_size": self.valves.SEARCH_CONTEXT_SIZE}

        # Increase timeout for 'Deep Research' model
        if model_id == "sonar-deep-research":
            self.timeout *= 10

        try:
            url = f"{self.valves.PERPLEXITY_API_BASE_URL}/chat/completions"
            async with httpx.AsyncClient(http2=True) as client:
                if body.get("stream", False):
                    # Streaming handling
                    async with client.stream("POST", url=url, json=payload, headers=headers, timeout=self.timeout) as r:
                        r.raise_for_status()
                        citations = []
                        async for line in r.aiter_lines():
                            ret_line, returned_citations = self._process_line(line)
                            yield ret_line
                            if not citations and returned_citations:
                                citations = returned_citations
                        if citations:
                            await self._emit_sources(citations, __event_emitter__)
                else:
                    # Non-stream handling
                    r = await client.post(url=url, json=payload, headers=headers, timeout=self.timeout)
                    r.raise_for_status()
                    response = r.json()
                    citations = response.get("citations", [])

                    formatted_response = {
                        "id": response["id"],
                        "model": response["model"],
                        "created": response["created"],
                        "usage": response.get("usage", {}),
                        "object": response["object"],
                        "choices": [
                            {
                                "index": choice["index"],
                                "finish_reason": choice["finish_reason"],
                                "message": {
                                    "role": choice["message"]["role"],
                                    "content": choice["message"]["content"],
                                },
                                "delta": {"role": "assistant", "content": ""},
                            }
                            for choice in response["choices"]
                        ],
                    }
                    yield formatted_response["choices"][0]["message"]["content"]

        except Exception as e:
            logging.exception("Perplexity API error!")
            yield f"Error: {e}"

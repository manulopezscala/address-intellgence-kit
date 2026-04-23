"""Registro in-process de las tools de Ubidata como servidor MCP del Claude Agent SDK."""

import json

from claude_agent_sdk import create_sdk_mcp_server, tool

from src.tools.ubidata import (
    TOOL_DEFINITIONS,
    _handle_check_coverage,
    _handle_normalize_address,
    _handle_validate_address,
)

_def_validate = next(t for t in TOOL_DEFINITIONS if t["name"] == "validate_address")
_def_normalize = next(t for t in TOOL_DEFINITIONS if t["name"] == "normalize_address")
_def_coverage = next(t for t in TOOL_DEFINITIONS if t["name"] == "check_coverage")


async def _with_retry(handler, args: dict) -> dict:
    result = await handler(**args)
    if result.get("error_type") and result.get("retryable"):
        result = await handler(**args)
    return result


@tool(
    name=_def_validate["name"],
    description=_def_validate["description"],
    input_schema=_def_validate["input_schema"],
)
async def _validate_address_tool(args: dict) -> dict:
    result = await _with_retry(_handle_validate_address, args)
    return {"content": [{"type": "text", "text": json.dumps(result)}]}


@tool(
    name=_def_normalize["name"],
    description=_def_normalize["description"],
    input_schema=_def_normalize["input_schema"],
)
async def _normalize_address_tool(args: dict) -> dict:
    result = await _with_retry(_handle_normalize_address, args)
    return {"content": [{"type": "text", "text": json.dumps(result)}]}


@tool(
    name=_def_coverage["name"],
    description=_def_coverage["description"],
    input_schema=_def_coverage["input_schema"],
)
async def _check_coverage_tool(args: dict) -> dict:
    result = await _handle_check_coverage(**args)
    return {"content": [{"type": "text", "text": json.dumps(result)}]}


UBIDATA_MCP_SERVER = create_sdk_mcp_server(
    name="ubidata",
    version="0.1.0",
    tools=[_validate_address_tool, _normalize_address_tool, _check_coverage_tool],
)

UBIDATA_TOOL_NAMES: list[str] = [
    _def_validate["name"],
    _def_normalize["name"],
    _def_coverage["name"],
]

#!/usr/bin/env python3
from typing import Annotated, List, Optional, Dict, Any
from pydantic import BaseModel, Field
import requests
import json
import os
from collections import defaultdict
from fastmcp import FastMCP
from mcp.types import CallToolResult, TextContent


mcp = FastMCP("sr-sg-mcp-server2")

SOURCEGRAPH_URL = "http://192.168.3.95"
ACCESS_TOKEN = "sgp_local_14007cabda4fd8ae47faf83d55a1d92f480c8681"
HEADERS = {
    "Content-Type": "application/json",
    "Authorization": f"token {ACCESS_TOKEN}"
}

def graphql_query(query, variables=None):
    """通用的 GraphQL 请求发送函数"""
    endpoint = f"{SOURCEGRAPH_URL}/.api/graphql"

    try:
        resp = requests.post(
            endpoint,
            json={"query": query, "variables": variables},
            headers=HEADERS,
            timeout=10
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        return {"error": str(e)}


# ----------------------------------------
# Common Data Structures
# ----------------------------------------
class CodeChunk(BaseModel):
    """
    Represents a snippet of code from a file.
    """
    startLine: int = Field(description="0-based line number where the chunk starts")
    endLine: int = Field(description="0-based line number where the chunk ends")
    content: str = Field(description="Code content with line number prefixes")


class FileBlock(BaseModel):
    """
    Represents a file with matching code chunks.
    """
    file: str = Field(description="File path within the repository")
    chunks: List[CodeChunk] = Field(description="List of matching code chunks")


# ----------------------------------------
# Tool 1: find_references
# ----------------------------------------
class FindReferencesInput(BaseModel):
    repo_name: str = Field(description="Repository name (e.g., B0_MP1/alps-release-b0.mp1.rc-aiot)")
    file_path: str = Field(description="File path containing the symbol")
    line: int = Field(description="0-indexed line number of the symbol")
    character: int = Field(description="0-indexed character offset of the symbol")
    branch: str = Field("dev_CipherLAB_F1", description="Branch name or commit hash")
    limit: int = Field(20, description="Maximum number of references to return")


class FindReferencesOutput(BaseModel):
    fileBlocks: List[FileBlock] = Field(description="List of files with symbol references")

@mcp.tool()
async def find_references(input: FindReferencesInput) -> FindReferencesOutput:
    """
    Finds references to a provided symbol in a repository.

    Use this tool when you have a specific symbol in mind (function, method, variable, class, etc.),
    you know where it is defined (a file path) and want to see where it is referenced / used in the codebase.

    This tool is the opposite of the go_to_definition tool - it finds references (usages) to a symbol given its definition.
    """

    gql = """
    query References($repo: String!, $rev: String!, $path: String!, $line: Int!, $character: Int!, $limit: Int!) {
      repository(name: $repo) {
        commit(rev: $rev) {
          blob(path: $path) {
            lsif {
              references(line: $line, character: $character, first: $limit) {
                nodes {
                  resource {
                    path
                    repository {
                      name
                    }
                  }
                  range {
                    start {
                      line
                      character
                    }
                    end {
                      line
                      character
                    }
                  }
                }
              }
            }
          }
        }
      }
    }
    """

    variables = {
        "repo": input.repo_name,
        "rev": input.branch,
        "path": input.file_path,
        "line": input.line,
        "character": input.character,
        "limit": input.limit
    }

    data = graphql_query(gql, variables)

    if "error" in data:
        return FindReferencesOutput(fileBlocks=[])

    file_blocks = []

    try:
        refs = data["data"]["repository"]["commit"]["blob"]["lsif"]["references"]["nodes"]
    except (KeyError, TypeError):
        refs = []

    try:

        for ref in refs:
            file_path = ref["resource"]["path"]
            start_line = ref["range"]["start"]["line"]  
            end_line = ref["range"]["end"]["line"]

            existing_block = next((b for b in file_blocks if b.file == file_path), None)

            chunk = CodeChunk(
                startLine=start_line,
                endLine=end_line,
                content=f"Reference: L{start_line}:{ref['range']['start']['character']} - L{end_line}:{ref['range']['end']['character']}"
            )

            if existing_block:
                existing_block.chunks.append(chunk)
            else:
                file_block = FileBlock(
                    file=file_path,
                    chunks=[chunk]
                )
                file_blocks.append(file_block)

    except Exception as e:
        print(f"Error parsing references: {str(e)}")

    return FindReferencesOutput(fileBlocks=file_blocks)


# ----------------------------------------
# Tool 2: go_to_definition
# ----------------------------------------
class GoToDefinitionInput(BaseModel):
    repo_name: str = Field(description="Repository name (e.g., B0_MP1/alps-release-b0.mp1.rc-aiot)")
    file_path: str = Field(description="File path where the symbol is used")
    line: int = Field(description="0-indexed line number of the symbol")
    character: int = Field(description="0-indexed character offset of the symbol")
    branch: str = Field("dev_CipherLAB_F1", description="Branch name or commit hash")


class GoToDefinitionOutput(BaseModel):
    fileBlocks: List[FileBlock] = Field(description="List containing the definition file")

@mcp.tool()
async def go_to_definition(input: GoToDefinitionInput) -> GoToDefinitionOutput:
    """
    Finds the definition of a specified symbol in a repository.

    Use this tool when you have a specific symbol in mind (function, method, variable, class, etc.), you know where it is used (a file path) and want to see its definition in the codebase.

    This tool is the opposite of the find_references tool - it finds the definition to a symbol given a reference/usage symbol.

    You should choose to use this tool over keyword_search or read_file when you have encountered a specific symbol (function, method, variable, class, etc.) that you want to understand better by seeing its definition.
    """

    gql = """
    query GetDefinition($repo: String!, $rev: String!, $path: String!, $line: Int!, $character: Int!) {
      repository(name: $repo) {
        commit(rev: $rev) {
          blob(path: $path) {
            lsif {
              definitions(line: $line, character: $character) {
                nodes {
                  resource {
                    path
                    repository {
                      name
                    }
                  }
                  range {
                    start {
                      line
                      character
                    }
                    end {
                      line
                      character
                    }
                  }
                }
              }
            }
          }
        }
      }
    }
    """

    variables = {
        "repo": input.repo_name,
        "rev": input.branch,
        "path": input.file_path,
        "line": input.line,
        "character": input.character
    }

    # 调用Sourcegraph GraphQL API
    data = graphql_query(gql, variables)

    if "error" in data:
        return GoToDefinitionOutput(fileBlocks=[])

    file_blocks = []

    try:
        defs = data["data"]["repository"]["commit"]["blob"]["lsif"]["definitions"]["nodes"]
    except (KeyError, TypeError):
        defs = []

    try:
        for d in defs:
            file_path = d["resource"]["path"]
            start_line = d["range"]["start"]["line"]  
            end_line = d["range"]["end"]["line"]

            chunk = CodeChunk(
                startLine=start_line,
                endLine=end_line,
                content=f"Definition: L{start_line}:{d['range']['start']['character']} - L{end_line}:{d['range']['end']['character']}" 
            )

            file_block = FileBlock(
                file=file_path,
                chunks=[chunk]
            )
            file_blocks.append(file_block)

    except Exception as e:
        print(f"Error parsing definition: {str(e)}")

    return GoToDefinitionOutput(fileBlocks=file_blocks)


# ----------------------------------------
# Tool 3: keyword_search
# ----------------------------------------
class KeywordSearchInput(BaseModel):
    query: str = Field(description="Search query string")
    repo_name: str = Field(description="Repository name to search in")
    branch: str = Field("dev_CipherLAB_F1", description="Branch name or commit hash")
    limit: int = Field(10, description="Maximum number of results to return")


class KeywordSearchOutput(BaseModel):
    blocks: List[Any] = Field(description="List of matching code blocks")

@mcp.tool()
async def keyword_search(input: KeywordSearchInput) -> KeywordSearchOutput:
    """
    Search for any keyword, string literal, or code pattern across all files in a repository using exact string matching.
    Use this when you have a text pattern to search for but don't have an exact symbol location.
    Do NOT use this if you already have a symbol location — use go_to_definition or find_references instead.
    Do NOT use this if you know the file path — use read_file instead.
    """
    # 构造查询字符串 - 参考main.py中的实现
    search_query = f"repo:{input.repo_name} rev:{input.branch} content:{input.query} count:{input.limit}"

    # GraphQL查询语句
    gql = """
    query Search($query: String!) {
      search(query: $query) {
        results {
          results {
            ... on FileMatch {
              file {
                path
                repository {
                  name
                }
              }
              lineMatches {
                preview
                lineNumber
              }
            }
          }
        }
      }
    }
    """

    variables = {
        "query": search_query
    }

    # 调用Sourcegraph GraphQL API
    data = graphql_query(gql, variables)

    if "error" in data:
        return KeywordSearchOutput(blocks=[])

    blocks = []

    try:
        results = data["data"]["search"]["results"]["results"]
    except (KeyError, TypeError):
        results = []

    try:
        for res in results:
            if "file" in res and "lineMatches" in res:
                file_path = res["file"]["path"]

                chunks = []
                for line_match in res.get("lineMatches", []):
                    chunk = CodeChunk(
                        startLine=line_match["lineNumber"],
                        endLine=line_match["lineNumber"],
                        content=line_match["preview"].rstrip()
                    )
                    chunks.append(chunk)

                if chunks:
                    file_block = FileBlock(
                        file=file_path,
                        chunks=chunks
                    )
                    blocks.append(file_block.model_dump())

    except Exception as e:
        print(f"Error parsing search results: {str(e)}")

    return KeywordSearchOutput(blocks=blocks)


# ----------------------------------------
# Tool 4: read_file
# ----------------------------------------
class ReadFileInput(BaseModel):
    repo_name: str = Field(description="Repository name (e.g., B0_MP1/alps-release-b0.mp1.rc-aiot)")
    file_path: str = Field(description="Full path to the file within the repository")
    branch: str = Field("dev_CipherLAB_F1", description="Branch name or commit hash")
    start_line: int = Field(0, description="0-indexed start line number")
    end_line: int = Field(-1, description="0-indexed end line number(inclusive this line), -1 means read to end")


class ReadFileOutput(BaseModel):
    content: str = Field(description="File content with each line prefixed by its 0-indexed line number. e.g. '0: first line text\n1: second line text\n2: third line text'")

@mcp.tool()
async def read_file(input: ReadFileInput) -> ReadFileOutput:
    """
    Reads the content of a file at a known path, with optional line range.
    Only use this when you already have an exact file path (from keyword_search, go_to_definition,  find_references or content you have). 
    Do NOT use to discover files or search text — use keyword_search instead.
    Returns content with line numbers. Max 500 lines per call; specify start_line/end_line for large files.
    """
    # GraphQL查询语句 - 参考main.py中的实现
    gql = """
    query ReadFile($repo: String!, $path: String!, $rev: String!) {
      repository(name: $repo) {
        commit(rev: $rev) {
          file(path: $path) {
            content
          }
        }
      }
    }
    """

    variables = {
        "repo": input.repo_name,
        "path": input.file_path,
        "rev": input.branch
    }

    # 调用Sourcegraph GraphQL API
    data = graphql_query(gql, variables)

    if "error" in data:
        return ReadFileOutput(content="")

    content = ""

    try:
        raw_content = data["data"]["repository"]["commit"]["file"]["content"]
    except (KeyError, TypeError):
        raw_content = ""

    try:
        lines = raw_content.splitlines()

        # 处理行范围
        end_line = len(lines) if input.end_line == -1 else min(input.end_line, len(lines))
        start_line = max(0, input.start_line)

        # 限制最大读取行数
        MAX_LINES = 500
        if end_line - start_line > MAX_LINES:
            #content=f"Error: Requested range too large ({end_line - start_line} lines). Please request smaller chunks."
            return ReadFileOutput(content="")

        selected_lines = lines[start_line:end_line+1]
        # 保持0-indexed行号，和其他MCP工具参数对齐，方便AI后续直接使用
        numbered_lines = [f"{i + start_line}: {line}" for i, line in enumerate(selected_lines)]
        content = "\n".join(numbered_lines)

    except Exception as e:
        print(f"Error reading file: {str(e)}")
        content = ""

    return ReadFileOutput(content=content)


# This function use to test all MCP tools
def testing():
    # ----------------------------------------
    # Example Usage
    # ----------------------------------------
    import asyncio

    # Example 1: Keyword search
    search_input = KeywordSearchInput(
        query="MTK_CAMERA_APP_VERSION_SEVEN",
        repo_name="B0_MP1/alps-release-b0.mp1.rc-aiot",
        branch="dev_CipherLAB_F1"
    )
    print("Keyword Search Input:")
    print(search_input.model_dump_json(indent=2))
    result = asyncio.run(keyword_search(search_input))
    print(result.model_dump_json(indent=2))
    print()

    # Example 2: Read file
    read_input = ReadFileInput(
        repo_name="B0_MP1/alps-release-b0.mp1.rc-aiot",
        file_path="alps/vendor/mediatek/proprietary/packages/apps/Camera2/common/src/com/mediatek/camera/common/CameraContext.java",
        start_line=11,
        end_line=12
    )
    print("Read File Input:")
    print(read_input.model_dump_json(indent=2))
    result = asyncio.run(read_file(read_input))
    # print(result.content) this is human readable
    print(result.model_dump_json(indent=2))
    print()

    # Example 3: Go to definition
    def_input = GoToDefinitionInput(
        repo_name="B0_MP1/alps-release-b0.mp1.rc-aiot",
        file_path="alps/vendor/mediatek/proprietary/packages/apps/Camera2/common/src/com/mediatek/camera/common/CameraContext.java",
        line=100,
        character=8
    )
    print("Go To Definition Input:")
    print(def_input.model_dump_json(indent=2))
    result = asyncio.run(go_to_definition(def_input))
    print(result.model_dump_json(indent=2))
    print()

    # Example 4: Find references
    ref_input = FindReferencesInput(
        repo_name="B0_MP1/alps-release-b0.mp1.rc-aiot",
        file_path="alps/vendor/mediatek/proprietary/packages/apps/Camera2/common/src/com/mediatek/camera/common/CameraContext.java",
        line=79,
        character=21
    )
    print("Find References Input:")
    print(ref_input.model_dump_json(indent=2))
    result = asyncio.run(find_references(ref_input))
    print(result.model_dump_json(indent=2))


# Main process
if __name__ == "__main__":
    # MCP Inspector调试页面URL写入http://127.0.0.1:8000/mcp即可
    IS_MCP_MODE = True
    # IS_MCP_MODE = False
    if IS_MCP_MODE:
        mcp.run(transport="http", host="0.0.0.0", port=8010)
    else:
        testing()

# END OF FILE

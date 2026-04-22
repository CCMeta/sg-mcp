#!/usr/bin/env python3
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field
import requests
import json
import os
from collections import defaultdict
from fastmcp import FastMCP

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
    """
    Input parameters for find_references tool.

    Use this tool to find all places where a symbol is used/called.
    Based on Sourcegraph LSIF/SCIP index data.
    """
    repo_name: str = Field(description="Repository name (e.g., B0_MP1/alps-release-b0.mp1.rc-aiot)")
    file_path: str = Field(description="File path containing the symbol")
    line: int = Field(description="0-indexed line number of the symbol")
    character: int = Field(description="0-indexed character offset of the symbol")
    branch: str = Field("dev_CipherLAB_F1", description="Branch name or commit hash")
    limit: int = Field(20, description="Maximum number of references to return")


class FindReferencesOutput(BaseModel):
    """
    Output structure for find_references tool.
    """
    fileBlocks: List[FileBlock] = Field(description="List of files with symbol references")

@mcp.tool()
async def find_references(input: FindReferencesInput) -> FindReferencesOutput:
    """Finds references to a provided symbol in a repository.

    Use this tool when you have a specific symbol in mind (function, method, variable, class, etc.),
    you know where it is defined (a file path) and want to see where it is referenced / used in the codebase.

    This tool is the opposite of the go_to_definition tool - it finds references (usages) to a symbol given its definition.

    Examples:
    <examples>
        <example>
            <user>Find where the CameraContext class is used. It's defined in alps/vendor/mediatek/proprietary/packages/apps/Camera2/common/src/com/mediatek/camera/common/CameraContext.java at line 79, character 21.</user>
            <response> [calls the find_references tool with repo_name="B0_MP1/alps-release-b0.mp1.rc-aiot", file_path="alps/vendor/mediatek/proprietary/packages/apps/Camera2/common/src/com/mediatek/camera/common/CameraContext.java", line=79, character=21]
            </response>
        </example>
    </examples>"""
    # GraphQL查询语句 - 参考main.py中的实现
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

    # 调用Sourcegraph GraphQL API
    data = graphql_query(gql, variables)

    if "error" in data:
        return FindReferencesOutput(fileBlocks=[])

    file_blocks = []
    try:
        refs = data.get("data", {}).get("repository", {}).get("commit", {}).get("blob", {}).get("lsif", {}).get("references", {}).get("nodes", [])

        for ref in refs:
            repo_name = ref["resource"]["repository"]["name"]
            file_path = ref["resource"]["path"]
            start_line = ref["range"]["start"]["line"]  
            end_line = ref["range"]["end"]["line"]

            # 查找是否已经有这个文件的block
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
    """
    Input parameters for go_to_definition tool.

    Use this tool when you encounter a symbol usage and want to find
    its actual definition in the codebase using Sourcegraph LSIF/SCIP data.

    Required parameters:
        repo_name: Repository name (e.g., 'B0_MP1/alps-release-b0.mp1.rc-aiot')
        file_path: File path where the symbol usage is located
        line: Line number (0-indexed) of the symbol
        character: Character offset (0-indexed) of the symbol

    Optional parameters:
        branch: Branch name or commit hash (default: "dev_CipherLAB_F1")
    """
    repo_name: str = Field(description="Repository name (e.g., B0_MP1/alps-release-b0.mp1.rc-aiot)")
    file_path: str = Field(description="File path where the symbol is used")
    line: int = Field(description="0-indexed line number of the symbol")
    character: int = Field(description="0-indexed character offset of the symbol")
    branch: str = Field("dev_CipherLAB_F1", description="Branch name or commit hash")


class GoToDefinitionOutput(BaseModel):
    """
    Output structure for go_to_definition tool.

    Returns:
        fileBlocks: List containing the file with the symbol definition,
                   with code chunks showing the complete definition
    """
    fileBlocks: List[FileBlock] = Field(description="List containing the definition file")

@mcp.tool()
async def go_to_definition(input: GoToDefinitionInput) -> GoToDefinitionOutput:
    """Finds the definition of a specified symbol in a repository.

    Use this tool when you have a specific symbol in mind (function, method, variable, class, etc.),
    you know where it is used (a file path) and want to see its definition in the codebase.

    This tool is the opposite of the find_references tool - it finds the definition
    to a symbol given a reference/usage symbol.

    You should choose to use this tool over keyword_search or read_file when you have encountered
    a specific symbol (function, method, variable, class, etc.) that you want to understand
    better by seeing its definition.

    Examples:
    <examples>
        <example>
            <user>I'm working in the B0_MP1/alps-release-b0.mp1.rc-aiot repo, in the file alps/vendor/mediatek/proprietary/packages/apps/Camera2/common/src/com/mediatek/camera/common/CameraContext.java at line 100, character 8. Where is this symbol defined?</user>
            <response> [calls the go_to_definition tool with repo_name="B0_MP1/alps-release-b0.mp1.rc-aiot", file_path="alps/vendor/mediatek/proprietary/packages/apps/Camera2/common/src/com/mediatek/camera/common/CameraContext.java", line=100, character=8]
            </response>
        </example>
    </examples>"""
    # GraphQL查询语句 - 参考main.py中的实现
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
        defs = data.get("data", {}).get("repository", {}).get("commit", {}).get("blob", {}).get("lsif", {}).get("definitions", {}).get("nodes", [])

        for d in defs:
            repo_name = d["resource"]["repository"]["name"]
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
    """
    Input parameters for keyword_search tool.

    Use this tool for exact keyword matching across codebases.
    Search code in the Sourcegraph instance to find file paths,
    function definitions, or specific code patterns.

    Required parameters:
        query: The search query (e.g., 'MTK_CAMERA_APP_VERSION_SEVEN')
        repo_name: The name of the repository to search in

    Optional parameters:
        branch: Branch name or commit hash (default: "dev_CipherLAB_F1")
        limit: Max number of results to return (default 10, max 20)
    """
    query: str = Field(description="Search query string")
    repo_name: str = Field(description="Repository name to search in")
    branch: str = Field("dev_CipherLAB_F1", description="Branch name or commit hash")
    limit: int = Field(10, description="Maximum number of results to return")


class KeywordSearchOutput(BaseModel):
    """
    Output structure for keyword_search tool.

    Returns:
        blocks: List of matching code blocks from various files
    """
    blocks: List[Any] = Field(description="List of matching code blocks")

@mcp.tool()
async def keyword_search(input: KeywordSearchInput) -> KeywordSearchOutput:
    """A keyword code search tool that helps you find relevant code snippets across repositories.

    Use this tool when you need to:
    - Find specific code with exact matching
    - Verify if certain code exists in the codebase
    - Find examples of code usage

    When NOT to use this tool:
    - For "how does this work" or understanding systems questions
    - For semantic or conceptual searches like 'authentication implementation'
    - For queries that are similar to natural language
    - When you are not sure if the term exists in the codebase

    Best practices:
    - Use a small (1-3) number of search terms
    - Use specific, descriptive search terms
    - Start with broader searches and narrow down

    Examples:
    <examples>
        <example>
            <user>Find all code using MTK_CAMERA_APP_VERSION_SEVEN</user>
            <response>calls the keyword search tool with query="MTK_CAMERA_APP_VERSION_SEVEN", repo_name="B0_MP1/alps-release-b0.mp1.rc-aiot"</response>
        </example>
    </examples>"""
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
    print(f"keyword_search data: {data}")
    if "error" in data:
        return KeywordSearchOutput(blocks=[])

    blocks = []
    try:
        results = data.get("data", {}).get("search", {}).get("results", {}).get("results", [])

        for res in results:
            if "file" in res and "lineMatches" in res:
                repo_name = res["file"]["repository"]["name"]
                file_path = res["file"]["path"]

                chunks = []
                for line_match in res.get("lineMatches", []):
                    chunk = CodeChunk(
                        startLine=line_match["lineNumber"],
                        endLine=line_match["lineNumber"],
                        content=line_match["preview"].strip()
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
    """
    Input parameters for read_file tool.

    Use this tool to read the raw content of a specific file from a repository.

    Required parameters:
        repo_name: Repository name (e.g., 'B0_MP1/alps-release-b0.mp1.rc-aiot')
        file_path: Full path to the file within the repository

    Optional parameters:
        branch: Branch name or commit hash (default: "dev_CipherLAB_F1")
        start_line: Optional start line number (0-indexed, default: 0)
        end_line: Optional end line number (0-indexed, -1 means read to end)
    """
    repo_name: str = Field(description="Repository name (e.g., B0_MP1/alps-release-b0.mp1.rc-aiot)")
    file_path: str = Field(description="Full path to the file within the repository")
    branch: str = Field("dev_CipherLAB_F1", description="Branch name or commit hash")
    start_line: int = Field(0, description="0-indexed start line number")
    end_line: int = Field(-1, description="0-indexed end line number, -1 means read to end")


class ReadFileOutput(BaseModel):
    """
    Output structure for read_file tool.

    Returns:
        content: File content as string, each line prefixed with line number
    """
    content: str = Field(description="File content with line number prefixes")

@mcp.tool()
async def read_file(input: ReadFileInput) -> ReadFileOutput:
    """Reads the content of a file in the repository.

    Returns the file content as a string. Each line is prefixed with its line number (0-indexed).

    IMPORTANT: Use this tool ONLY when you have already located the specific file.

    You can optionally specify a line range to read only a portion of the file.
    Automatically limits reads to 500 lines maximum to prevent context overload.

    Examples:
    <examples>
        <example>
            <user>Read the CameraContext.java file</user>
            <response>Calls the read file tool with repo_name="B0_MP1/alps-release-b0.mp1.rc-aiot", file_path="alps/vendor/mediatek/proprietary/packages/apps/Camera2/common/src/com/mediatek/camera/common/CameraContext.java"</response>
        </example>
        <example>
            <user>Read lines 0-100 of CameraContext.java</user>
            <response>Calls the read file tool with repo_name="B0_MP1/alps-release-b0.mp1.rc-aiot", file_path="alps/vendor/mediatek/proprietary/packages/apps/Camera2/common/src/com/mediatek/camera/common/CameraContext.java", start_line=0, end_line=100</response>
        </example>
    </examples>"""
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
        return ReadFileOutput(content=f"Read Error: {data['error']}")

    content = ""
    try:
        raw_content = data.get("data", {}).get("repository", {}).get("commit", {}).get("file", {}).get("content")

        if raw_content is None:
            return ReadFileOutput(content="File not found or empty.")

        lines = raw_content.split('\n')

        # 处理行范围
        end_line = len(lines) if input.end_line == -1 else min(input.end_line, len(lines))
        start_line = max(0, input.start_line)

        # 限制最大读取行数
        MAX_LINES = 500
        if end_line - start_line > MAX_LINES:
            return ReadFileOutput(
                content=f"Error: Requested range too large ({end_line - start_line} lines). Please request smaller chunks."
            )

        selected_lines = lines[start_line:end_line]
        # 保持0-indexed行号，和其他MCP工具参数对齐，方便AI后续直接使用
        numbered_lines = [f"{i + start_line}: {line}" for i, line in enumerate(selected_lines)]
        content = "\n".join(numbered_lines)

    except Exception as e:
        content = f"Error reading file: {str(e)}"

    return ReadFileOutput(content=content)


# ----------------------------------------
# Example Usage
# ----------------------------------------
@mcp.tool()
def test ():
    """This is a Sourcegraph search tool for open source code indexed on sourcegraph.com. Use this for searching public repositories and open source projects, not internal or private code. Finds references to a provided symbol in a repository.
A symbol is any code identifier, such as a function name, variable name, or class name.
It handles overloading by leveraging compiler information to ensure references are to the exact symbol requested. It can even handle cross-repository references.

Returns a list of usages of that symbol, specifically:
- Where the symbol is referenced in the code
- The file and line number of each reference
- Surrounding context of each reference to help understand its usage
If the symbol is not found, returns "Symbol not found"

This tool is the opposite of the go_to_definition tool - it finds references (usages) to a symbol given its definition.

You should use this tool when you have a specific symbol in mind (function, method, variable, class, etc.), you know where it is defined (a file path) and want to see where it is referenced / used in the codebase.

You should choose to use this tool over keyword_search, nls_search or read_file when you have encountered the definition of a specific symbol (function, variable, class)
and you want to see how that specific symbol is used throughout the codebase, understand code flow or performing impact analysis.

Examples:
<examples>
        <example>
                <user>Find where the AbstractPaymentProcessorClass is used. It's defined in src/processors/AbstractPaymentProcessor.ts in the ecommerce/payment-service repository.</user>
                <response> [calls the find references tool with repo="ecommerce/payment-service", path="src/processors/AbstractPaymentProcessor.ts", symbol="AbstractPaymentProcessor"]
                 {
                "repo": "ecommerce/payment-service",
                "path": "src/processors/StripePaymentProcessor.ts",
                "rev": "HEAD",
                "chunks": [
                                        {
                                                "startLine": 2,
                                                "endLine": 2,
                                                "content": "2: import { AbstractPaymentProcessor } from './AbstractPaymentProcessor';\n"
                                        },
                    {
                        "startLine": 102,
                        "endLine": 103,
                        "content": "102: class StripePaymentProcessor extends AbstractPaymentProcessor {\n103: \tprivate readonly Status status;\n"
                    }
                ]
            }
                </response>
        </example>
</examples>"""
    return "Hello, world!"

if __name__ == "__main__":
    """Example demonstrating how to use the tools with local Sourcegraph instance"""
    # MCP Inspector调试页面URL写入http://127.0.0.1:8000/mcp即可
    mcp.run(transport="http", host="0.0.0.0", port=8010)
    import asyncio

    # Example 1: Keyword search
    search_input = KeywordSearchInput(
        query="MTK_CAMERA_APP_VERSION_SEVEN",
        repo_name="B0_MP1/alps-release-b0.mp1.rc-aiot",
        branch="dev_CipherLAB_F1"
    )
    print("Keyword Search Input:")
    print(search_input.model_dump_json(indent=2))
    # result = asyncio.run(keyword_search(search_input))
    # print(result.model_dump_json(indent=2))
    print()

    # Example 2: Read file
    read_input = ReadFileInput(
        repo_name="B0_MP1/alps-release-b0.mp1.rc-aiot",
        file_path="alps/vendor/mediatek/proprietary/packages/apps/Camera2/common/src/com/mediatek/camera/common/CameraContext.java",
        start_line=0,
        end_line=10
    )
    print("Read File Input:")
    print(read_input.model_dump_json(indent=2))
    # result = asyncio.run(read_file(read_input))
    # print(result.content)
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
    # result = asyncio.run(go_to_definition(def_input))
    # print(result.model_dump_json(indent=2))
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

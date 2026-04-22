import time

import requests
import json

from typing import Dict, Any, List
from collections import defaultdict
from fastmcp import FastMCP

# 配置信息
SOURCEGRAPH_URL = "http://192.168.3.95" # 替换你的地址
ACCESS_TOKEN = "sgp_local_14007cabda4fd8ae47faf83d55a1d92f480c8681" # 替换你的Token

# 初始化 MCP Server
mcp = FastMCP("my-fastmcp-server")

def graphql_query(query, variables=None):
    """通用的 GraphQL 请求发送函数"""
    headers = {"Authorization": f"token {ACCESS_TOKEN}"}
    endpoint = f"{SOURCEGRAPH_URL}/.api/graphql"
    
    try:
        resp = requests.post(endpoint, json={"query": query, "variables": variables}, headers=headers, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def keyword_search(query: str, repo_name: str, branch: str = "dev_CipherLAB_F1", limit: int = 10) -> str:
    """
    Search code in the Sourcegraph instance.
    Use this to find file paths, function definitions, or specific code patterns.
    
    Args:
        query: The search query (e.g., 'repo:B0_MP1/alps-release-b0.mp1.rc-aiot rev:dev_CipherLAB_F1 content:MTK_CAMERA_APP_VERSION_SEVEN')
        limit: Max number of results to return (default 10, max 20)
    Returns:
        A string of JSON formatted
    """
    query = f"repo:{repo_name} rev:{branch} content:{query} count:{limit}"
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
    data = graphql_query(gql, {"query": query})
    
    # print(data)
    if "error" in data:
        return f"Search Error: {data['error']}"
        
    results = data.get("data", {}).get("search", {}).get("results", {}).get("results", [])
    return results
    if not results:
        return "No code found matching your query."

    # 格式化输出给 LLM 看
    output = []
    for res in results:
        repo = res['file']['repository']['name']
        path = res['file']['path']
        output.append(f"--- File: {repo}/{path} ---")
        for match in res.get('lineMatches', []):
            output.append(f"Line {match['lineNumber']}: {match['preview'].strip()}")
        output.append("")
    
    return "\n".join(output)


@mcp.tool()
def read_file(repo_name: str, file_path: str, branch: str = "dev_CipherLAB_F1", start_line: int = 0, end_line: int = -1) -> str:
    """
    Read the raw content of a specific file from a repository.
    
    Args:
        repo_name: The name of the repository (e.g., 'B0_MP1/alps-release-b0.mp1.rc-aiot')
        file_path: The full path to the file (e.g., 'alps/vendor/mediatek/proprietary/packages/apps/Camera2/common/src/com/mediatek/camera/common/CameraContext.java')
        branch: Branch name or commit hash (default: "dev_CipherLAB_F1")
        start_line: Optional start line number (0-indexed)
        end_line: Optional end line number (0-indexed). If -1, reads to end.
    Returns:
        A string of JSON formatted
    """

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
    data = graphql_query(gql, {"repo": repo_name, "path": file_path, "rev": branch})
    
    if "error" in data:
        return f"Read Error: {data['error']}"
    # print (data)
    content = data.get("data", {}).get("repository", {}).get("commit", {}).get("file", {}).get("content")
    return
    if content is None:
        return "File not found or empty."

    lines = content.split('\n')
    
    # 处理行号截取 (避免读取整个巨大的文件)
    if end_line == -1: 
        end_line = len(lines)
    
    # 限制读取最大行数，防止 Context 爆炸
    MAX_LINES = 500
    if end_line - start_line > MAX_LINES:
        return f"Error: Requested range too large ({end_line - start_line} lines). Please request smaller chunks."

    selected_lines = lines[start_line:end_line]
    numbered_lines = [f"{i + start_line + 1}: {line}" for i, line in enumerate(selected_lines)]
    
    return "\n".join(numbered_lines)


@mcp.tool()
def go_to_definition(repo_name: str, file_path: str, line: int, character: int, branch: str = "dev_CipherLAB_F1") -> str:
    """
    Go to Definition: Find where a symbol is defined using Sourcegraph SCIP/LSIF graph data.
    Use this when you see a function/class usage and want to see its implementation.
    
    Args:
        repo_name: The repository name (e.g., 'B0_MP1/alps-release-b0.mp1.rc-aiot')
        file_path: The file path where the symbol usage is located (e.g., 'alps/vendor/mediatek/proprietary/packages/apps/Camera2/common/src/com/mediatek/camera/common/CameraContext.java')
        line: The line number (0-indexed)
        character: The character offset (0-indexed) of the symbol
        branch: Branch name or commit hash (default: "dev_CipherLAB_F1")
    Returns:
        A string of JSON formatted
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
                  }
                  range {
                    start { line, character }
                    end { line, character }
                  }
                }
              }
            }
          }
        }
      }
    }
    """
    # 这里的 line 和 character 是查询的符号的位置
    data = graphql_query(gql, {"repo": repo_name, "path": file_path, "line": line, "character": character, "rev": branch})
    
    # print(data)
    # 解析复杂的 GraphQL 返回
    try:
        defs = data.get("data", {}).get("repository", {}).get("commit", {}).get("blob", {}).get("lsif", {}).get("definitions", {}).get("nodes")
        # defs = data['data']['repository']['commit']['blob']['lsif']['definitions']['nodes']
        if not defs:
            return "No definition found (SCIP index might be missing)."
        return defs
        output = []
        for d in defs:
            d_repo = d['resource']['repository']['name']
            d_path = d['resource']['path']
            d_line = d['range']['start']['line']
            output.append(f"Definition found at: {d_repo}/{d_path} (Line {d_line})")
            
        return "\n".join(output)
    except Exception as e:
        return f"Error parsing graph data: {str(e)}"


@mcp.tool()
def get_references(repo_name: str, file_path: str, line: int, character: int, branch: str = "dev_CipherLAB_F1", limit: int = 20) -> str:
    """
    Find References: Find all places where a symbol is used/called.

    Args:
        repo_name: The repository name (e.g., 'B0_MP1/alps-release-b0.mp1.rc-aiot')
        file_path: The file path where the symbol definition is located (e.g., 'alps/vendor/mediatek/proprietary/packages/apps/Camera2/common/src/com/mediatek/camera/common/CameraContext.java')
        line: The line number (0-indexed)
        character: The character offset (0-indexed) of the symbol
        branch: Branch name or commit hash (default: "dev_CipherLAB_F1")
        limit: The maximum number of references to return (default: 20)
    Returns:
        A string of JSON formatted
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
                  }
                  range {
                    start { line }
                  }
                }
              }
            }
          }
        }
      }
    }
    """
    data = graphql_query(gql, {
        "repo": repo_name, "path": file_path, 
        "line": line, "character": character, "rev": branch, "limit": limit
    })

    print(data)
    # 解析复杂的 GraphQL 返回
    
    try:
        refs = data['data']['repository']['commit']['blob']['lsif']['references']['nodes']
        if not refs:
            return "No references found."
            
        output = [f"Found {len(refs)} references:"]
        for r in refs:
            # r_repo = r['resource']['repository']['name']
            r_path = r['resource']['path']
            r_line = r['range']['start']['line']
            output.append(f"- {r_path} (Line {r_line})")
            
        return "\n".join(output)
    except Exception as e:
        return f"Error parsing graph data: {str(e)}"


if __name__ == "__main__":
    print("Starting MCP server...")
    # mcp.run()
    # MCP Inspector调试页面URL写入http://127.0.0.1:8000/mcp即可
    # mcp.run(transport="http", host="0.0.0.0", port=8010)
    time_start = time.time()

    # DEMO keyword_search
    # 查找MTK A16-SYSTEM的分支dev_CipherLAB_F1下的关键词MTK_CAMERA_APP_VERSION_SEVEN
    # print(keyword_search("MTK_CAMERA_APP_VERSION_SEVEN", "B0_MP1/alps-release-b0.mp1.rc-aiot", "dev_CipherLAB_F1"))

    # DEMO read_file
    # 读取展锐A16-SYSTEM的分支dev_CipherLAB_F1下的文件ShadowMaskSettings.kt
    print(read_file("B0_MP1/alps-release-b0.mp1.rc-aiot", "alps/vendor/mediatek/proprietary/packages/apps/Camera2/common/src/com/mediatek/camera/common/CameraContext.java", "dev_CipherLAB_F1", 0, 100))

    # DEMO go_to_definition
    # 查找私人库GetKnownMAUI的分支master下的关键词的定义位置
    # print(go_to_definition("B0_MP1/alps-release-b0.mp1.rc-aiot", "alps/vendor/mediatek/proprietary/packages/apps/Camera2/common/src/com/mediatek/camera/common/CameraContext.java", 100, 8, "dev_CipherLAB_F1"))

    # print(get_references("B0_MP1/alps-release-b0.mp1.rc-aiot", "alps/vendor/mediatek/proprietary/packages/apps/Camera2/common/src/com/mediatek/camera/common/CameraContext.java", 79, 21111, "dev_CipherLAB_F1", 20))

    print(f"[CCMETA] Search cost {time.time() - time_start:.2f} seconds.")

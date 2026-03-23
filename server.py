import time

import requests
import json

from typing import Dict, Any, List
from collections import defaultdict
from fastmcp import FastMCP

# 配置信息
SOURCEGRAPH_URL = "http://172.30.11.46" # 替换你的地址
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
def keyword_search(query: str, limit: int = 10) -> str:
    """
    Search code in the Sourcegraph instance.
    Use this to find file paths, function definitions, or specific code patterns.
    
    Args:
        query: The search query (e.g., 'repo:android type:file content:BluetoothAdapter')
        limit: Max number of results to return (default 10, max 20)
    """
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
    # 自动加上 limit，防止 LLM 被大量数据淹没
    full_query = f"{query} count:{limit}"
    data = graphql_query(gql, {"query": full_query})
    
    print(data)
    if "error" in data:
        return f"Search Error: {data['error']}"
        
    results = data.get("data", {}).get("search", {}).get("results", {}).get("results", [])
    
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
def read_file(repo_name: str, file_path: str, branch: str = "HEAD", start_line: int = 0, end_line: int = -1) -> str:
    """
    Read the raw content of a specific file from a repository.
    
    Args:
        repo_name: The name of the repository (e.g., 'android/platform/frameworks/base')
        file_path: The full path to the file (e.g., 'core/java/android/os/Looper.java')
        branch: Branch name or commit hash (default: "HEAD")
        start_line: Optional start line number (0-indexed)
        end_line: Optional end line number (0-indexed). If -1, reads to end.
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
def go_to_definition(repo_name: str, file_path: str, line: int, character: int, branch: str = "HEAD") -> str:
    """
    Go to Definition: Find where a symbol is defined using Sourcegraph SCIP/LSIF graph data.
    Use this when you see a function/class usage and want to see its implementation.
    
    Args:
        repo_name: The repository name
        file_path: The file path where the symbol usage is located
        line: The line number (0-indexed)
        character: The character offset (0-indexed) of the symbol
        branch: Branch name or commit hash (default: "HEAD")
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
                    repository { name }
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
        defs = data['data']['repository']['commit']['blob']['lsif']['definitions']['nodes']
        if not defs:
            return "No definition found (SCIP index might be missing)."
            
        output = []
        for d in defs:
            d_repo = d['resource']['repository']['name']
            d_path = d['resource']['path']
            d_line = d['range']['start']['line']
            output.append(f"Definition found at: {d_repo}/{d_path} (Line {d_line})")
            
        return "\n".join(output)
    except Exception as e:
        return f"Error parsing graph data: {str(e)}"


if __name__ == "__main__":
    print("Starting MCP server...")
    # mcp.run()
    # MCP Inspector调试页面URL写入http://127.0.0.1:8000/mcp即可
    mcp.run(transport="http", host="0.0.0.0", port=8000)

    # DEMO keyword_search
    # 查找展锐A16-SYSTEM的分支dev_Common_UI_A16下的关键词ShadowMaskSettings
    # print(keyword_search("repo:SPRDROID16_SYS_MAIN_W25.22.4@dev_Common_UI_A16 content:ShadowMaskSettings"))

    # DEMO read_file
    # 读取展锐A16-SYSTEM的分支dev_Common_UI_A16下的文件ShadowMaskSettings.kt
    # print(read_file("SPRD_B/SPRDROID16_SYS_MAIN_W25.22.4", "alps/packages/apps/Settings/src-ui/com/android/settings/shadowmask/ShadowMaskSettings.kt", "dev_Common_UI_A16", 0, 100))

    # DEMO go_to_definition
    # 查找展锐A16-SYSTEM的分支dev_Common_UI_A16下的关键词的定义位置
    # print(go_to_definition("github.com/CCMeta/GetKnownMAUI", "GetKnownMAUI/Views/MenuPage.xaml.cs", 26, 45, "HEAD"))

    # print(sourcegraph_keyword_search("repo:^github.com/CCMeta/cm31$ ccmeta"))
    time_start = time.time()
    # print(sourcegraph_keyword_search("repo:^github\.com/CCMeta/flv\.js$ NativePlayer"))
    print(f"[CCMETA] Search cost {time.time() - time_start:.2f} seconds.")
    # print(keyword_search("function", 10))
    # print(read_file("github.com/CCMeta/ShadowMask", "app/src/main/java/com/example/shadowmask/PrivacyOverlayService.kt", "master", 0, 100))
    #print (go_to_definition("github.com/CCMeta/ShadowMask", "app/src/main/java/com/example/shadowmask/PrivacyOverlayService.kt", 90, 20, "master"))

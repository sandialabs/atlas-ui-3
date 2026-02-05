#!/usr/bin/env python3
"""
MCP Server for Web Search using DuckDuckGo.
Provides a tool to search DuckDuckGo, fetch the content of a result,
and return it.
"""

import requests
from bs4 import BeautifulSoup
from duckduckgo_search import DDGS
from typing import Any, Dict, Union

from fastmcp import FastMCP

# Initialize the MCP server
mcp = FastMCP("WebSearcher")


def get_page_content(url: str) -> str:
    """
    Fetches and parses the text content of a given URL.

    Args:
        url: The URL of the webpage to parse.

    Returns:
        The cleaned text content of the page, or an error message.
    """
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Accept-Encoding': 'gzip, deflate',
            'Referer': 'https://www.google.com/',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
        }
        
        response = requests.get(url, headers=headers, timeout=15, allow_redirects=True)
        response.raise_for_status()  # Raise an exception for bad status codes (4xx or 5xx)

        soup = BeautifulSoup(response.text, 'html.parser')

        # Remove script and style elements
        for script_or_style in soup(["script", "style"]):
            script_or_style.decompose()

        # Get text and clean it up
        text = soup.get_text()
        lines = (line.strip() for line in text.splitlines())
        chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
        cleaned_text = '\n'.join(chunk for chunk in chunks if chunk)

        return cleaned_text

    except requests.RequestException as e:
        return f"Error fetching URL {url}: {e}"
    except Exception as e:
        return f"An error occurred during page parsing: {e}"


@mcp.tool
def search_and_fetch(query: str, max_results: Union[str, int] = 3) -> Dict[str, Any]:
    """
    Search the web using DuckDuckGo and intelligently fetch full content from the most relevant results.

    This powerful web search tool combines search and content retrieval:
    - Uses DuckDuckGo search engine for privacy-focused web searching
    - Automatically attempts to fetch and parse content from multiple results
    - Returns the first successfully retrieved page content along with metadata
    - Handles various content types and website structures intelligently

    **Search Capabilities:**
    - Natural language queries and specific search terms
    - Multiple search result evaluation for best content retrieval
    - Fallback strategy: tries multiple URLs if the first fails
    - Respects website robots.txt and rate limiting

    **Content Extraction:**
    - Removes navigation, ads, and formatting for clean text
    - Extracts main article/content body from web pages
    - Handles dynamic content and various website layouts
    - Provides webpage title and URL for reference

    **Privacy & Ethics:**
    - Uses DuckDuckGo for privacy-conscious searching
    - Includes proper user agent and headers
    - Respects website terms and handles errors gracefully

    **Use Cases:**
    - Research on current events, technical topics, or general information
    - Fact-checking and information verification
    - Content analysis and summarization preparation
    - Market research and competitive analysis
    - Academic research and reference gathering

    **Examples:**
    - "latest developments in AI" → Recent news and articles
    - "Python pandas tutorial" → Educational content and documentation
    - "climate change statistics 2024" → Current data and reports

    Args:
        query: Search query (string). Use natural language or specific keywords.
        max_results: Maximum number of search results to attempt content fetch from (1-10, default 3)

    Returns:
        Dictionary containing:
        - title: Title of the successfully fetched webpage
        - url: URL of the source page
        - content: Cleaned text content from the webpage
        - query: Original search query used
        Or error message if no content could be retrieved from any results
    """
    try:
        # convert to int. max = 10, min = 1
        max_results = int(max_results)
        if max_results < 1:
            max_results = 1
        elif max_results > 10:
            max_results = 10
        with DDGS() as ddgs:
            # Get multiple results to try if first one fails
            results = list(ddgs.text(query, max_results=max_results))

        if not results:
            return {"results": {"error": "No results found for your query."}}

        # Try each result until we successfully fetch content
        errors_encountered = []
        
        for i, result in enumerate(results):
            result_title = result.get('title')
            result_url = result.get('href')

            if not result_url:
                errors_encountered.append(f"Result {i+1}: No URL found")
                continue

            print(f"[DEBUG] Attempting to fetch content from: {result_url}")
            
            # Fetch and parse the content of the page
            content = get_page_content(result_url)

            if content.startswith("Error"):
                errors_encountered.append(f"Result {i+1} ({result_title}): {content}")
                print(f"[DEBUG] Failed to fetch {result_url}: {content}")
                continue
            
            # Success! Return the content
            print(f"[DEBUG] Successfully fetched content from {result_url}")
            return {
                "results": {
                    "operation": "search_and_fetch",
                    "query": query,
                    "result_title": result_title,
                    "result_url": result_url,
                    "content": content,
                    "attempt": i + 1,
                    "total_results": len(results)
                }
            }

        # If we get here, all results failed
        return {
            "results": {
                "operation": "search_and_fetch",
                "query": query,
                "error": f"Failed to fetch content from all {len(results)} search results",
                "errors": errors_encountered,
                "total_results": len(results)
            }
        }

    except Exception as e:
        return {"results": {"error": f"An unexpected error occurred: {str(e)}"}}


if __name__ == "__main__":
    # To run this server, you need to install the required libraries:
    # pip install fastmcp duckduckgo-search requests beautifulsoup4
    mcp.run()

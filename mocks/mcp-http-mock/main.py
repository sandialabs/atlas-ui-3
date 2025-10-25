#!/usr/bin/env python3
"""
FastMCP HTTP Server - Database Simulation Example

This MCP server simulates database select statement retrieval using FastMCP.
It provides tools for querying simulated tables and retrieving data.
"""

import json
import re
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional
from dataclasses import dataclass
from fastmcp import FastMCP

# Simulated database data
SIMULATED_DATABASE = {
    "users": [
        {"id": 1, "name": "Alice Johnson", "email": "alice@example.com", "department": "Engineering", "salary": 85000, "hire_date": "2022-01-15"},
        {"id": 2, "name": "Bob Smith", "email": "bob@example.com", "department": "Marketing", "salary": 75000, "hire_date": "2021-08-20"},
        {"id": 3, "name": "Carol Davis", "email": "carol@example.com", "department": "Engineering", "salary": 92000, "hire_date": "2020-03-10"},
        {"id": 4, "name": "David Wilson", "email": "david@example.com", "department": "Sales", "salary": 68000, "hire_date": "2023-02-28"},
        {"id": 5, "name": "Eva Brown", "email": "eva@example.com", "department": "Engineering", "salary": 95000, "hire_date": "2019-11-05"},
    ],
    "orders": [
        {"id": 101, "user_id": 1, "product": "Laptop", "amount": 1299.99, "order_date": "2024-01-15", "status": "completed"},
        {"id": 102, "user_id": 2, "product": "Mouse", "amount": 29.99, "order_date": "2024-01-20", "status": "completed"},
        {"id": 103, "user_id": 1, "product": "Keyboard", "amount": 89.99, "order_date": "2024-02-01", "status": "pending"},
        {"id": 104, "user_id": 3, "product": "Monitor", "amount": 459.99, "order_date": "2024-02-10", "status": "completed"},
        {"id": 105, "user_id": 4, "product": "Headphones", "amount": 199.99, "order_date": "2024-02-15", "status": "shipped"},
    ],
    "products": [
        {"id": 1, "name": "Laptop", "category": "Electronics", "price": 1299.99, "stock": 25},
        {"id": 2, "name": "Mouse", "category": "Electronics", "price": 29.99, "stock": 150},
        {"id": 3, "name": "Keyboard", "category": "Electronics", "price": 89.99, "stock": 75},
        {"id": 4, "name": "Monitor", "category": "Electronics", "price": 459.99, "stock": 40},
        {"id": 5, "name": "Headphones", "category": "Electronics", "price": 199.99, "stock": 60},
    ]
}

# Initialize FastMCP server
mcp = FastMCP(
    name="Database Simulator",
    instructions="""
    This server simulates a database with select statement capabilities.
    Use the available tools to query users, orders, and products tables.
    You can filter, sort, and limit results as needed.
    """
)

@dataclass
class QueryResult:
    """Represents a database query result"""
    rows: List[Dict[str, Any]]
    total_count: int
    execution_time_ms: float
    query_summary: str

def simulate_query_execution_time() -> float:
    """Simulate database query execution time"""
    import random
    return round(random.uniform(5.0, 50.0), 2)

def apply_filters(data: List[Dict], filters: Dict[str, Any]) -> List[Dict]:
    """Apply filters to the data"""
    filtered_data = data.copy()
    
    for field, value in filters.items():
        if value is not None:
            filtered_data = [
                row for row in filtered_data 
                if str(row.get(field, '')).lower() == str(value).lower()
            ]
    
    return filtered_data

def apply_sorting(data: List[Dict], sort_by: Optional[str], sort_order: str = "asc") -> List[Dict]:
    """Apply sorting to the data"""
    if not sort_by or sort_by not in (data[0].keys() if data else []):
        return data
    
    reverse = sort_order.lower() == "desc"
    return sorted(data, key=lambda x: x.get(sort_by, ''), reverse=reverse)

@mcp.tool
def select_users(
    limit: int = 10,
    department: Optional[str] = None,
    min_salary: Optional[int] = None,
    sort_by: Optional[str] = None,
    sort_order: str = "asc"
) -> str:
    """
    Select users from the users table with optional filtering and sorting.
    
    Args:
        limit: Maximum number of records to return (default: 10)
        department: Filter by department (Engineering, Marketing, Sales)
        min_salary: Minimum salary filter
        sort_by: Field to sort by (id, name, salary, hire_date)
        sort_order: Sort order - 'asc' or 'desc' (default: asc)
    
    Returns:
        JSON formatted query results
    """
    start_time = datetime.now()
    
    # Get base data
    data = SIMULATED_DATABASE["users"].copy()
    
    # Apply filters
    if department:
        data = [row for row in data if row["department"].lower() == department.lower()]
    
    if min_salary:
        data = [row for row in data if row["salary"] >= min_salary]
    
    # Apply sorting
    data = apply_sorting(data, sort_by, sort_order)
    
    # Apply limit
    total_count = len(data)
    data = data[:limit]
    
    # Create result
    execution_time = simulate_query_execution_time()
    
    query_summary = f"SELECT * FROM users"
    filters = []
    if department:
        filters.append(f"department = '{department}'")
    if min_salary:
        filters.append(f"salary >= {min_salary}")
    if filters:
        query_summary += f" WHERE {' AND '.join(filters)}"
    if sort_by:
        query_summary += f" ORDER BY {sort_by} {sort_order.upper()}"
    query_summary += f" LIMIT {limit}"
    
    result = QueryResult(
        rows=data,
        total_count=total_count,
        execution_time_ms=execution_time,
        query_summary=query_summary
    )
    
    return json.dumps({
        "results": {
            "success": True,
            "query": result.query_summary,
            "execution_time_ms": result.execution_time_ms,
            "total_rows": result.total_count,
            "returned_rows": len(result.rows),
            "data": result.rows
        }
    }, indent=2)

@mcp.tool
def select_orders(
    limit: int = 10,
    user_id: Optional[int] = None,
    status: Optional[str] = None,
    min_amount: Optional[float] = None,
    sort_by: Optional[str] = None,
    sort_order: str = "asc"
) -> str:
    """
    Select orders from the orders table with optional filtering and sorting.
    
    Args:
        limit: Maximum number of records to return (default: 10)
        user_id: Filter by user ID
        status: Filter by order status (completed, pending, shipped)
        min_amount: Minimum order amount filter
        sort_by: Field to sort by (id, user_id, amount, order_date, status)
        sort_order: Sort order - 'asc' or 'desc' (default: asc)
    
    Returns:
        JSON formatted query results
    """
    start_time = datetime.now()
    
    # Get base data
    data = SIMULATED_DATABASE["orders"].copy()
    
    # Apply filters
    if user_id:
        data = [row for row in data if row["user_id"] == user_id]
    
    if status:
        data = [row for row in data if row["status"].lower() == status.lower()]
    
    if min_amount:
        data = [row for row in data if row["amount"] >= min_amount]
    
    # Apply sorting
    data = apply_sorting(data, sort_by, sort_order)
    
    # Apply limit
    total_count = len(data)
    data = data[:limit]
    
    # Create result
    execution_time = simulate_query_execution_time()
    
    query_summary = f"SELECT * FROM orders"
    filters = []
    if user_id:
        filters.append(f"user_id = {user_id}")
    if status:
        filters.append(f"status = '{status}'")
    if min_amount:
        filters.append(f"amount >= {min_amount}")
    if filters:
        query_summary += f" WHERE {' AND '.join(filters)}"
    if sort_by:
        query_summary += f" ORDER BY {sort_by} {sort_order.upper()}"
    query_summary += f" LIMIT {limit}"
    
    result = QueryResult(
        rows=data,
        total_count=total_count,
        execution_time_ms=execution_time,
        query_summary=query_summary
    )
    
    return json.dumps({
        "results": {
            "success": True,
            "query": result.query_summary,
            "execution_time_ms": result.execution_time_ms,
            "total_rows": result.total_count,
            "returned_rows": len(result.rows),
            "data": result.rows
        }
    }, indent=2)

@mcp.tool
def select_products(
    limit: int = 10,
    category: Optional[str] = None,
    min_price: Optional[float] = None,
    min_stock: Optional[int] = None,
    sort_by: Optional[str] = None,
    sort_order: str = "asc"
) -> str:
    """
    Select products from the products table with optional filtering and sorting.
    
    Args:
        limit: Maximum number of records to return (default: 10)
        category: Filter by product category
        min_price: Minimum price filter
        min_stock: Minimum stock quantity filter
        sort_by: Field to sort by (id, name, price, stock)
        sort_order: Sort order - 'asc' or 'desc' (default: asc)
    
    Returns:
        JSON formatted query results
    """
    start_time = datetime.now()
    
    # Get base data
    data = SIMULATED_DATABASE["products"].copy()
    
    # Apply filters
    if category:
        data = [row for row in data if row["category"].lower() == category.lower()]
    
    if min_price:
        data = [row for row in data if row["price"] >= min_price]
    
    if min_stock:
        data = [row for row in data if row["stock"] >= min_stock]
    
    # Apply sorting
    data = apply_sorting(data, sort_by, sort_order)
    
    # Apply limit
    total_count = len(data)
    data = data[:limit]
    
    # Create result
    execution_time = simulate_query_execution_time()
    
    query_summary = f"SELECT * FROM products"
    filters = []
    if category:
        filters.append(f"category = '{category}'")
    if min_price:
        filters.append(f"price >= {min_price}")
    if min_stock:
        filters.append(f"stock >= {min_stock}")
    if filters:
        query_summary += f" WHERE {' AND '.join(filters)}"
    if sort_by:
        query_summary += f" ORDER BY {sort_by} {sort_order.upper()}"
    query_summary += f" LIMIT {limit}"
    
    result = QueryResult(
        rows=data,
        total_count=total_count,
        execution_time_ms=execution_time,
        query_summary=query_summary
    )
    
    return json.dumps({
        "results": {
            "success": True,
            "query": result.query_summary,
            "execution_time_ms": result.execution_time_ms,
            "total_rows": result.total_count,
            "returned_rows": len(result.rows),
            "data": result.rows
        }
    }, indent=2)

@mcp.tool
def execute_custom_query(sql_query: str) -> str:
    """
    Execute a custom SQL-like query (simulation only).
    
    Args:
        sql_query: SQL-like query string (for demonstration purposes)
    
    Returns:
        JSON formatted simulation result
    """
    # Simple query parser simulation
    query_lower = sql_query.lower().strip()
    
    # Simulate query validation
    if not query_lower.startswith('select'):
        return json.dumps({
            "results": {
                "success": False,
                "error": "Only SELECT statements are supported in this simulation",
                "query": sql_query
            }
        }, indent=2)
    
    # Extract table name (very basic parsing)
    table_match = re.search(r'from\s+(\w+)', query_lower)
    if not table_match:
        return json.dumps({
            "results": {
                "success": False,
                "error": "Could not identify table in query",
                "query": sql_query
            }
        }, indent=2)
    
    table_name = table_match.group(1)
    
    if table_name not in SIMULATED_DATABASE:
        return json.dumps({
            "results": {
                "success": False,
                "error": f"Table '{table_name}' not found. Available tables: {list(SIMULATED_DATABASE.keys())}",
                "query": sql_query
            }
        }, indent=2)
    
    # For this simulation, just return the first 5 rows of the requested table
    data = SIMULATED_DATABASE[table_name][:5]
    execution_time = simulate_query_execution_time()
    
    return json.dumps({
        "results": {
            "success": True,
            "query": sql_query,
            "execution_time_ms": execution_time,
            "note": "This is a simulated query execution",
            "total_rows": len(SIMULATED_DATABASE[table_name]),
            "returned_rows": len(data),
            "data": data
        }
    }, indent=2)

@mcp.tool
def get_database_schema() -> str:
    """
    Get the database schema information.
    
    Returns:
        JSON formatted schema information
    """
    schema = {
        "database": "simulated_db",
        "tables": {}
    }
    
    for table_name, rows in SIMULATED_DATABASE.items():
        if rows:
            # Infer schema from first row
            sample_row = rows[0]
            columns = {}
            for col_name, col_value in sample_row.items():
                if isinstance(col_value, int):
                    col_type = "INTEGER"
                elif isinstance(col_value, float):
                    col_type = "FLOAT"
                else:
                    col_type = "VARCHAR(255)"
                
                columns[col_name] = {
                    "type": col_type,
                    "nullable": True
                }
            
            schema["tables"][table_name] = {
                "columns": columns,
                "row_count": len(rows)
            }
    
    return json.dumps({"results": schema}, indent=2)

# Resource to provide database information
@mcp.resource("database://info")
def database_info() -> str:
    """Provides general information about the simulated database"""
    info = {
        "database_name": "simulated_db",
        "type": "In-Memory Simulation",
        "tables": list(SIMULATED_DATABASE.keys()),
        "total_records": sum(len(table) for table in SIMULATED_DATABASE.values()),
        "last_updated": datetime.now().isoformat(),
        "description": "A simulated database for demonstrating MCP server capabilities"
    }
    return json.dumps(info, indent=2)

import argparse

# Entry point
if __name__ == "__main__":
    print("Starting FastMCP Database Simulator Server...")
    print("Available transports:")
    print("  - HTTP (default): python server.py")
    print("  - STDIO: python server.py --stdio")
    print("  - SSE: python server.py --sse")

    parser = argparse.ArgumentParser(description="Start FastMCP Database Simulator Server")
    parser.add_argument(
        "--stdio", action="store_true", help="Use STDIO transport"
    )
    parser.add_argument(
        "--sse", action="store_true", help="Use SSE transport"
    )
    args = parser.parse_args()

    if args.stdio:
        print("\n🚀 Starting STDIO server...")
        mcp.run()  # Default STDIO transport
    elif args.sse:
        print("\n🚀 Starting SSE server on http://127.0.0.1:8005/sse")
        mcp.run(
            transport="sse",
            host="127.0.0.1",
            port=8005,
        )
    else:
        print("\n🚀 Starting HTTP server on http://127.0.0.1:8005/mcp")
        mcp.run(
            transport="http",
            host="127.0.0.1",
            port=8005,
            path="/mcp"
        )
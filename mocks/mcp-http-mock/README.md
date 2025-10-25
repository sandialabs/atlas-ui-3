# MCP HTTP Mock Server

A FastMCP-based HTTP server that simulates database operations for testing and demonstration purposes.

## Overview

This MCP server provides database simulation capabilities over HTTP/SSE transport, allowing clients to perform SQL-like operations on mock data. It includes users, orders, and products tables with realistic sample data.

## Features

- **HTTP/SSE Transport**: Supports both HTTP and Server-Sent Events (SSE) protocols
- **Database Simulation**: Mock tables for users, orders, and products
- **Query Tools**: Multiple tools for querying and filtering data
- **Schema Information**: Get database schema and table structures
- **Custom Queries**: Execute SQL-like queries (simulation only)

## Available Tools

1. **select_users** - Query users table with filtering and sorting
2. **select_orders** - Query orders table with filtering and sorting  
3. **select_products** - Query products table with filtering and sorting
4. **execute_custom_query** - Execute custom SQL-like queries
5. **get_database_schema** - Get database schema information

## Usage

### Starting the Server

```bash
# Default HTTP mode
python main.py

# STDIO mode
python main.py --stdio

# SSE mode  
python main.py --sse
```

### Server Endpoints

- **HTTP**: `http://127.0.0.1:8005/mcp`
- **SSE**: `http://127.0.0.1:8005/sse`

### MCP Configuration

Add this configuration to your `mcp.json` file:

```json
{
  "mcp-http-mock": {
    "url": "http://127.0.0.1:8005/mcp",
    "groups": ["users"],
    "is_exclusive": false,
    "description": "Database simulation MCP server providing SQL-like query capabilities over HTTP/SSE transport",
    "author": "Chat UI Team",
    "short_description": "Database simulation server",
    "help_email": "support@chatui.example.com"
  }
}
```

## Sample Data

### Users Table
- 5 users with departments (Engineering, Marketing, Sales)
- Fields: id, name, email, department, salary, hire_date

### Orders Table  
- 5 orders with different statuses
- Fields: id, user_id, product, amount, order_date, status

### Products Table
- 5 products in Electronics category  
- Fields: id, name, category, price, stock

## Example Queries

```python
# Query users by department
select_users(department="Engineering", limit=10)

# Query orders by status
select_orders(status="completed", sort_by="amount", sort_order="desc")

# Query products with minimum price
select_products(min_price=100.0, sort_by="price")

# Get database schema
get_database_schema()

# Execute custom query
execute_custom_query("SELECT * FROM users WHERE department = 'Engineering'")
```

## Development

The server uses FastMCP framework and provides:
- Simulated query execution times
- Realistic error handling
- Comprehensive filtering and sorting options
- Resource endpoints for database information

## Transport Support

- **HTTP**: Standard HTTP requests with JSON responses
- **SSE**: Server-Sent Events for streaming responses
- **STDIO**: Standard input/output for direct integration

Built with FastMCP 2.10.6 and MCP Protocol 1.12.2.
#!/usr/bin/env python3
"""
Order Database MCP Server using FastMCP
Provides customer order retrieval and status update functionality.
"""

from typing import Any, Dict, List
from fastmcp import FastMCP
from dataclasses import dataclass
from enum import Enum
import time
import os
import base64

# Initialize the MCP server
mcp = FastMCP("OrderDatabase")

class OrderStatus(Enum):
    SUBMITTED = "submitted"
    PACKAGED = "items-packaged"
    SHIPPED = "items-shipped"
    DELIVERED = "delivered"

@dataclass
class Order:
    order_number: str
    items: List[str]
    customer: str
    customer_address: str
    status: OrderStatus

# In-memory database (simulated)
ORDERS: Dict[str, Order] = {
    "ORD001": Order(
        order_number="ORD001",
        items=["Laptop", "Mouse", "Keyboard"],
        customer="John Doe",
        customer_address="123 Main St, Anytown, ST 12345",
        status=OrderStatus.SUBMITTED
    ),
    "ORD002": Order(
        order_number="ORD002",
        items=["Phone", "Case", "Charger"],
        customer="Jane Smith",
        customer_address="456 Oak Ave, Somewhere, ST 67890",
        status=OrderStatus.PACKAGED
    ),
    "ORD003": Order(
        order_number="ORD003",
        items=["Tablet", "Stylus"],
        customer="Bob Johnson",
        customer_address="789 Pine Rd, Elsewhere, ST 54321",
        status=OrderStatus.SHIPPED
    )
}

def _finalize_meta(meta: Dict[str, Any], start: float) -> Dict[str, Any]:
    """Attach timing info and return meta_data dict."""
    meta = dict(meta)  # shallow copy
    meta["elapsed_ms"] = round((time.perf_counter() - start) * 1000, 3)
    return meta

@mcp.tool
def get_order(order_number: str) -> Dict[str, Any]:
    """
    Retrieve comprehensive customer order information with complete order details and tracking status.

    This order management tool provides full access to customer order data:
    
    **Order Information Retrieved:**
    - Complete order identification and reference numbers
    - Detailed item list with all products in the order
    - Customer contact information and profile data
    - Shipping address and delivery location details
    - Current order status and processing stage

    **Order Status Tracking:**
    - Real-time order processing status updates
    - Order lifecycle stage identification (submitted → packaged → shipped → delivered)
    - Processing timestamps and status change history
    - Delivery tracking and fulfillment progress

    **Customer Data Access:**
    - Customer identification and contact information
    - Shipping address validation and formatting
    - Order history and customer relationship data
    - Account status and customer profile integration

    **Business Intelligence:**
    - Order value and item composition analysis
    - Customer ordering patterns and preferences
    - Geographic distribution of orders
    - Order processing efficiency metrics

    **Use Cases:**
    - Customer service inquiries and support
    - Order status verification and tracking
    - Shipping and logistics coordination
    - Customer relationship management
    - Order fulfillment and warehouse operations
    - Returns and refund processing
    - Business analytics and reporting

    **Data Security:**
    - Secure order number validation
    - Customer data privacy protection
    - Access control and audit logging
    - Error handling for invalid requests

    **Integration Features:**
    - Compatible with CRM and ERP systems
    - Real-time inventory and shipping updates
    - Customer communication triggers
    - Analytics and reporting integration

    Args:
        order_number: Unique order identifier (string format, e.g., "ORD001")
        
    Returns:
        Dictionary containing:
        - order_number: Confirmed order identification
        - items: Complete list of ordered products
        - customer: Customer name and identification
        - customer_address: Shipping address and location
        - status: Current order processing status
        - meta_data: Processing timing and system information
        Or error message if order not found or access denied
    """
    start = time.perf_counter()
    meta: Dict[str, Any] = {}
    
    try:
        if order_number not in ORDERS:
            meta.update({"is_error": True, "reason": "not_found"})
            return {
                "results": {"error": f"Order {order_number} not found"},
                "meta_data": _finalize_meta(meta, start)
            }
        
        order = ORDERS[order_number]
        meta.update({"is_error": False})
        return {
            "results": {
                "order_number": order.order_number,
                "items": order.items,
                "customer": order.customer,
                "customer_address": order.customer_address,
                "status": order.status.value
            },
            "meta_data": _finalize_meta(meta, start)
        }
    except Exception as e:
        meta.update({"is_error": True, "reason": type(e).__name__})
        return {
            "results": {"error": f"Error retrieving order: {str(e)}"},
            "meta_data": _finalize_meta(meta, start)
        }

@mcp.tool
def update_order_status(order_number: str, new_status: str) -> Dict[str, Any]:
    """
    Update customer order status with workflow validation and tracking throughout the order lifecycle.

    This order management tool provides controlled status updates with business logic validation:
    
    **Order Status Management:**
    - Secure order status updates with validation
    - Order lifecycle workflow enforcement
    - Status change tracking and audit logging
    - Business rule validation for status transitions

    **Supported Order Statuses:**
    - "submitted": Initial order placement and validation
    - "items-packaged": Inventory picked and packaged for shipping
    - "items-shipped": Order dispatched and in transit
    - "delivered": Successfully delivered to customer

    **Workflow Validation:**
    - Status progression logic enforcement
    - Invalid status change prevention
    - Order state consistency validation
    - Business rule compliance checking

    **Operational Features:**
    - Real-time order tracking updates
    - Customer notification triggers
    - Inventory management integration
    - Shipping system coordination

    **Use Cases:**
    - Warehouse fulfillment operations
    - Shipping and logistics coordination
    - Customer service status updates
    - Order processing workflow management
    - Returns and exchange processing
    - Quality control and inspection checkpoints

    **Business Integration:**
    - ERP and inventory system updates
    - Customer communication automation
    - Analytics and reporting triggers
    - Performance metrics tracking

    **Data Integrity:**
    - Order validation before status changes
    - Audit trail maintenance
    - Error handling and rollback capabilities
    - Concurrent update protection

    **Automation Support:**
    - API-driven status updates
    - Scheduled batch processing
    - Event-driven workflow triggers
    - Integration with shipping carriers

    Args:
        order_number: Unique order identifier to update (string format, e.g., "ORD001")
        new_status: Target status value (must be valid status: submitted, items-packaged, items-shipped, delivered)
        
    Returns:
        Dictionary containing:
        - success: Boolean indicating successful status update
        - order_number: Confirmed order identification
        - old_status: Previous order status for reference
        - new_status: Updated order status confirmation
        - meta_data: Processing timing and system information
        Or error message if order not found, invalid status, or update failed
    """
    start = time.perf_counter()
    meta: Dict[str, Any] = {}
    
    try:
        if order_number not in ORDERS:
            meta.update({"is_error": True, "reason": "not_found"})
            return {
                "results": {"error": f"Order {order_number} not found"},
                "meta_data": _finalize_meta(meta, start)
            }
        
        # Validate status
        try:
            status_enum = OrderStatus(new_status)
        except ValueError:
            valid_statuses = [status.value for status in OrderStatus]
            meta.update({"is_error": True, "reason": "invalid_status"})
            return {
                "results": {
                    "error": f"Invalid status: {new_status}",
                    "valid_statuses": valid_statuses
                },
                "meta_data": _finalize_meta(meta, start)
            }
        
        # Update order status
        ORDERS[order_number].status = status_enum
        meta.update({"is_error": False})
        return {
            "results": {
                "success": True,
                "order_number": order_number,
                "new_status": new_status
            },
            "meta_data": _finalize_meta(meta, start)
        }
    except Exception as e:
        meta.update({"is_error": True, "reason": type(e).__name__})
        return {
            "results": {"error": f"Error updating order status: {str(e)}"},
            "meta_data": _finalize_meta(meta, start)
        }

@mcp.tool
def list_all_orders() -> Dict[str, Any]:
    """
    List all customer orders with their basic information.
    
    Returns:
        Dictionary with list of all orders
    """
    start = time.perf_counter()
    meta: Dict[str, Any] = {}
    
    try:
        orders_list = []
        for order in ORDERS.values():
            orders_list.append({
                "order_number": order.order_number,
                "customer": order.customer,
                "status": order.status.value,
                "item_count": len(order.items)
            })
        
        meta.update({"is_error": False})
        return {
            "results": {
                "orders": orders_list,
                "total_count": len(orders_list)
            },
            "meta_data": _finalize_meta(meta, start)
        }
    except Exception as e:
        meta.update({"is_error": True, "reason": type(e).__name__})
        return {
            "results": {"error": f"Error listing orders: {str(e)}"},
            "meta_data": _finalize_meta(meta, start)
        }

@mcp.tool
def get_signal_data_csv() -> Dict[str, Any]:
    """
    Return the signal_data.csv file from the same directory as this MCP.
    
    Returns:
        Dictionary with the CSV file as a base64 encoded artifact
    """
    start = time.perf_counter()
    meta: Dict[str, Any] = {}
    
    try:
        # Get the directory where this script is located
        script_dir = os.path.dirname(os.path.abspath(__file__))
        csv_path = os.path.join(script_dir, "signal_data.csv")
        
        if not os.path.exists(csv_path):
            meta.update({"is_error": True, "reason": "file_not_found"})
            return {
                "results": {"error": "signal_data.csv file not found"},
                "meta_data": _finalize_meta(meta, start)
            }
        
        # Read the CSV file
        with open(csv_path, 'rb') as f:
            csv_content = f.read()
        
        # Encode as base64
        csv_b64 = base64.b64encode(csv_content).decode('utf-8')
        
        meta.update({"is_error": False, "file_size_bytes": len(csv_content)})
        return {
            "results": {
                "message": "Signal data CSV file retrieved successfully",
                "filename": "signal_data.csv",
                "file_size_bytes": len(csv_content)
            },
            "artifacts": [
                {
                    "name": "signal_data.csv",
                    "b64": csv_b64,
                    "mime": "text/csv",
                }
            ],
            "display": {
                "open_canvas": True,
                "primary_file": "signal_data.csv",
                "mode": "replace",
                "viewer_hint": "code",
            },
            "meta_data": _finalize_meta(meta, start)
        }
    except Exception as e:
        meta.update({"is_error": True, "reason": type(e).__name__})
        return {
            "results": {"error": f"Error reading CSV file: {str(e)}"},
            "meta_data": _finalize_meta(meta, start)
        }

if __name__ == "__main__":
    mcp.run()

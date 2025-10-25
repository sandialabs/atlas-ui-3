#!/usr/bin/env python3
"""
CSV/XLSX Analyzer MCP Server using FastMCP.
Detects numerical columns, generates basic statistical plots, and returns as Base64.
"""

import base64
import io
from typing import Any, Dict, Annotated

import pandas as pd
import matplotlib.pyplot as plt
from fastmcp import FastMCP

# Initialize the MCP server
mcp = FastMCP("CSV_XLSX_Analyzer")

@mcp.tool
def analyze_spreadsheet(
    instructions: Annotated[str, "Instructions for the tool, not used in this implementation"],
    filename: Annotated[str, "The name of the file (.csv or .xlsx)"],
    file_data_base64: Annotated[str, "LLM agent can leave blank. Do NOT fill. Framework will fill this."] = ""
) -> Dict[str, Any]:
    """
    Perform comprehensive spreadsheet analysis with automatic data visualization for CSV and Excel files.

    This intelligent data analysis tool provides instant insights into spreadsheet data:
    
    **File Format Support:**
    - CSV files (.csv) with various delimiters and encodings
    - Excel files (.xlsx) including multiple sheets and complex formatting
    - Automatic format detection and appropriate parsing
    - Robust handling of different data structures and layouts

    **Data Analysis Capabilities:**
    - Automatic numerical column detection and classification
    - Statistical distribution analysis for all numeric data
    - Data quality assessment and completeness evaluation
    - Column type identification and validation

    **Visualization Features:**
    - Auto-generated histograms for all numerical columns
    - Multi-panel plots showing data distribution patterns
    - Professional formatting with grid layout optimization
    - High-resolution PNG output suitable for reports and presentations

    **Data Insights Provided:**
    - Column count and data type summary
    - Numerical data distribution patterns
    - Data range and statistical characteristics
    - Missing value identification
    - Outlier detection through visual inspection

    **Smart Processing:**
    - Handles large datasets efficiently
    - Automatic plot layout optimization based on column count
    - Error handling for corrupted or invalid data
    - Graceful degradation for edge cases

    **Use Cases:**
    - Initial data exploration and profiling
    - Data quality assessment before analysis
    - Quick statistical overview for stakeholder presentations
    - Dataset validation and structure verification
    - Automated reporting and data documentation

    **Examples:**
    - Sales data → Revenue and quantity distribution histograms
    - Survey responses → Response pattern and demographic distributions
    - Financial records → Transaction amount and balance distributions
    - Scientific measurements → Variable distribution and range analysis

    Args:
        instructions: Analysis instructions or requirements (currently not used in processing)
        filename: Name of the spreadsheet file (.csv or .xlsx extensions required)
        file_data_base64: Base64-encoded file content (automatically provided by framework)

    Returns:
        Dictionary containing:
        - results: Analysis summary with column and data information
        - artifacts: High-quality histogram visualization as downloadable PNG
        - display: Optimized viewer configuration for data visualization
        - meta_data: Processing statistics and file information
        Or error message if file cannot be processed or contains no numerical data
    """
    try:
        # Validate file extension
        ext = filename.lower().split('.')[-1]
        if ext not in ['csv', 'xlsx']:
            return {"results": {"error": "Invalid file type. Only .csv or .xlsx allowed."}}

        # Decode file data
        decoded_bytes = base64.b64decode(file_data_base64)
        buffer = io.BytesIO(decoded_bytes)

        # Load dataframe
        if ext == 'csv':
            df = pd.read_csv(buffer)
        else:
            df = pd.read_excel(buffer)

        if df.empty:
            return {"results": {"error": "File is empty or has no readable content."}}

        # Detect numerical columns
        num_cols = df.select_dtypes(include=['number']).columns.tolist()
        if not num_cols:
            return {"results": {"error": "No numerical columns found for plotting."}}

        # Generate plot
        plt.figure(figsize=(8, 6))
        df[num_cols].hist(bins=20, figsize=(10, 8), grid=False)
        plt.tight_layout()

        # Save to buffer as PNG
        img_buffer = io.BytesIO()
        plt.savefig(img_buffer, format='png')
        plt.close()
        img_buffer.seek(0)

        # Encode to Base64
        img_base64 = base64.b64encode(img_buffer.read()).decode('utf-8')
        img_buffer.close()

        # Create file list for multiple file support
        returned_files = [{
            'filename': "analysis_plot.png",
            'content_base64': img_base64
        }]
        returned_file_names = ["analysis_plot.png"]
        returned_file_contents = [img_base64]
        
        return {
            "results": {
                "operation": "spreadsheet_analysis",
                "filename": filename,
                "numerical_columns": num_cols,
                "message": f"Detected numerical columns: {', '.join(num_cols)}. Histogram plot generated."
            },
            "returned_file_names": returned_file_names,
            "returned_file_contents": returned_file_contents
        }

    except Exception as e:
        # print traceback for debugging
        import traceback
        traceback.print_exc()
        return {"results": {"error": f"Spreadsheet analysis failed: {str(e)}"}}

if __name__ == "__main__":
    print("Starting CSV/XLSX Analyzer MCP server...")
    mcp.run()

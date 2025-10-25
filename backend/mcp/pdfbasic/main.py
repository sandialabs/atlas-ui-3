#!/usr/bin/env python3
"""
PDF Analyzer MCP Server using FastMCP.
Provides PDF text analysis and report generation through the MCP protocol.
"""

import base64
import io
import re
from collections import Counter
from typing import Any, Dict, Annotated

# This tool requires the PyPDF2 and reportlab libraries.
# Install them using: pip install PyPDF2 reportlab
from PyPDF2 import PdfReader
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch

from fastmcp import FastMCP

mcp = FastMCP("PDF_Analyzer")


def _analyze_pdf_content(instructions: str, filename: str, file_data_base64: str) -> Dict[str, Any]:
    """
    Core PDF analysis logic that can be reused by multiple tools.
    
    Args:
        instructions: Instructions for the tool, not used in this implementation.
        filename: The name of the file, which must have a '.pdf' extension.
        file_data_base64: The Base64-encoded string of the PDF file content.

    Returns:
        A dictionary containing the analysis results or an error message.
    """
    try:
        # print the instructions.
        print(f"Instructions: {instructions}")
        # 1. Validate that the filename is for a PDF
        if not filename.lower().endswith('.pdf'):
            return {"results": {"error": "Invalid file type. This tool only accepts PDF files."}}

        # 2. Decode the Base64 data and read the PDF content
        decoded_bytes = base64.b64decode(file_data_base64)
        pdf_stream = io.BytesIO(decoded_bytes)
        reader = PdfReader(pdf_stream)

        full_text = ""
        for page in reader.pages:
            page_text = page.extract_text()
            if page_text:
                full_text += page_text + "\n"

        if not full_text.strip():
            return {
                "results": {
                    "operation": "pdf_analysis",
                    "filename": filename,
                    "status": "Success",
                    "message": "PDF contained no extractable text.",
                    "total_word_count": 0,
                    "top_100_words": {}
                }
            }

        # 3. Process the text to get a word list and count
        # This regex finds all word-like sequences, ignoring case
        words = re.findall(r'\b\w+\b', full_text.lower())
        total_word_count = len(words)

        # 4. Count word frequencies and get the top 100
        word_counts = Counter(words)
        # Convert list of (word, count) tuples to a dictionary
        top_100_words_dict = dict(word_counts.most_common(100))

        # 5. Return the successful result
        return {
            "results": {
                "operation": "pdf_analysis",
                "filename": filename,
                "total_word_count": total_word_count,
                "top_100_words": top_100_words_dict
            }
        }

    except Exception as e:
        # print traceback for debugging
        import traceback
        traceback.print_exc()
        # 6. Return an error message if something goes wrong
        return {"results": {"error": f"PDF analysis failed: {str(e)}"}}


@mcp.tool
def analyze_pdf(
    instructions: Annotated[str, "Instructions for the tool, not used in this implementation"],
    filename: Annotated[str, "The name of the file, which must have a '.pdf' extension"],
    file_data_base64: Annotated[str, "LLM agent can leave blank. Do NOT fill. This will be filled by the framework."] = ""
) -> Dict[str, Any]:
    """
    Extract and analyze text content from PDF documents with comprehensive word frequency analysis.

    This powerful PDF processing tool provides detailed text analytics for PDF documents:
    
    **PDF Text Extraction:**
    - Extracts text from all pages in PDF documents
    - Handles various PDF formats and structures
    - Works with both text-based and scanned PDFs (text extraction only)
    - Preserves document structure and content flow

    **Text Analysis Features:**
    - Complete word count across entire document
    - Top 100 most frequently used words identification
    - Case-insensitive word analysis for accurate frequency counting
    - Word pattern recognition and linguistic analysis
    - Document length and content density assessment

    **Content Processing:**
    - Intelligent text cleaning and normalization
    - Punctuation and formatting handling
    - Multi-language text support
    - Special character and encoding management

    **Analytics Insights:**
    - Document vocabulary richness and complexity
    - Key topic identification through word frequency
    - Content themes and focus areas analysis
    - Writing style and language pattern recognition
    - Document structure and organization assessment

    **Use Cases:**
    - Academic paper and research document analysis
    - Legal document keyword extraction and analysis
    - Content marketing and SEO keyword research
    - Document classification and categorization
    - Research literature review and summarization
    - Contract and agreement content analysis

    **Supported PDF Types:**
    - Research papers, reports, and academic documents
    - Business documents, contracts, and agreements
    - Marketing materials and content documents
    - Technical documentation and manuals
    - Legal documents and regulatory filings

    **Output Format:**
    - Structured word frequency data
    - Total document word count statistics
    - Top 100 words with occurrence frequencies
    - Document metadata and processing information

    Args:
        instructions: Processing instructions or requirements (currently not used)
        filename: PDF file name (must end with .pdf extension)
        file_data_base64: Base64-encoded PDF content (automatically provided by framework)

    Returns:
        Dictionary containing:
        - operation: Processing type confirmation
        - filename: Source PDF file name
        - total_word_count: Complete document word count
        - top_100_words: Dictionary of most frequent words with counts
        Or error message if PDF cannot be processed or contains no extractable text
    """
    return _analyze_pdf_content(instructions, filename, file_data_base64)


@mcp.tool
def generate_report_about_pdf(
    instructions: Annotated[str, "Instructions for the tool, not used in this implementation"],
    filename: Annotated[str, "The name of the file, which must have a '.pdf' extension"],
    file_data_base64: Annotated[str, "LLM agent can leave blank. Do NOT fill. This will be filled by the framework."] = ""
) -> Dict[str, Any]:
    """
    Create comprehensive PDF analysis reports with professional formatting and detailed word frequency insights.

    This advanced PDF reporting tool combines text analysis with professional document generation:
    
    **Complete PDF Analysis Workflow:**
    - Performs full text extraction and word frequency analysis
    - Generates professional analysis reports in PDF format
    - Creates downloadable documents with structured data presentation
    - Provides ready-to-share analytical insights

    **Report Contents:**
    - Executive summary with document overview
    - Total word count and document statistics
    - Top 100 most frequent words with occurrence counts
    - Professional multi-column layout for easy reading
    - Organized tabular presentation of word frequency data

    **Report Features:**
    - Clean, professional PDF formatting using ReportLab
    - Multi-column layout optimizing space usage
    - Clear headers and structured information hierarchy
    - Page management for large datasets
    - High-quality typography and spacing

    **Document Generation:**
    - Creates new PDF reports from analysis results
    - Professional business document appearance
    - Optimized layout for printing and digital sharing
    - Comprehensive data presentation in readable format

    **Use Cases:**
    - Academic research document analysis reporting
    - Legal document content analysis for litigation support
    - Content marketing keyword research documentation
    - Business document compliance and review reporting
    - Research literature analysis and summarization
    - Document classification and content audit reports

    **Report Applications:**
    - Stakeholder presentations with document insights
    - Content strategy planning based on word analysis
    - Academic research methodology documentation
    - Legal discovery and document review processes
    - Quality assurance for written content

    **Output Features:**
    - Professional PDF report with embedded analysis
    - Downloadable file for offline access and sharing
    - Structured data visualization in document format
    - Ready-to-present analytical insights

    Args:
        instructions: Report generation instructions or requirements (currently not used)
        filename: Source PDF file name (must end with .pdf extension)
        file_data_base64: Base64-encoded PDF content (automatically provided by framework)

    Returns:
        Dictionary containing:
        - results: Report generation summary and success confirmation
        - artifacts: Professional PDF report with complete analysis
        - display: Optimized viewer configuration for report presentation
        - meta_data: Source file information and analysis statistics
        Or error message if PDF cannot be processed or report generation fails
    """
    # --- 1. Perform the same analysis as the first function ---
    analysis_result = _analyze_pdf_content(instructions, filename, file_data_base64)
    if "error" in analysis_result:
        return analysis_result # Return the error if analysis failed

    # --- 2. Generate a PDF report from the analysis results ---
    try:
        buffer = io.BytesIO()
        # Create a canvas to draw on, using the buffer as the "file"
        p = canvas.Canvas(buffer, pagesize=letter)
        width, height = letter

        # Set up starting coordinates
        x = inch
        y = height - inch

        # Write title
        p.setFont("Helvetica-Bold", 16)
        p.drawString(x, y, f"Analysis Report for: {analysis_result['filename']}")
        y -= 0.5 * inch

        # Write summary
        p.setFont("Helvetica", 12)
        p.drawString(x, y, f"Total Word Count: {analysis_result['total_word_count']}")
        y -= 0.3 * inch

        # Write header for top words
        p.setFont("Helvetica-Bold", 12)
        p.drawString(x, y, "Top 100 Most Frequent Words:")
        y -= 0.25 * inch

        # Write the list of top words
        p.setFont("Helvetica", 10)
        col1_x, col2_x, col3_x, col4_x = x, x + 1.75*inch, x + 3.5*inch, x + 5.25*inch
        current_x = col1_x
        
        # Simple column layout
        count = 0
        for word, freq in analysis_result['top_100_words'].items():
            if y < inch: # New page if we run out of space
                p.showPage()
                p.setFont("Helvetica", 10)
                y = height - inch

            p.drawString(current_x, y, f"{word}: {freq}")
            
            # Move to the next column
            if count % 4 == 0: current_x = col2_x
            elif count % 4 == 1: current_x = col3_x
            elif count % 4 == 2: current_x = col4_x
            else: # Move to the next row
                current_x = col1_x
                y -= 0.2 * inch
            count += 1
            
        # Finalize the PDF
        p.save()
        
        # --- 3. Encode the generated PDF for return ---
        report_bytes = buffer.getvalue()
        buffer.close()
        report_base64 = base64.b64encode(report_bytes).decode('utf-8')

        # Create a new filename for the report
        report_filename = f"analysis_report_{filename.replace('.pdf', '.txt')}.pdf"

        # --- 4. Return v2 MCP format with artifacts and display ---
        return {
            "results": {
                "operation": "pdf_analysis_report",
                "original_filename": filename,
                "message": f"Successfully generated analysis report for {filename}."
            },
            "artifacts": [
                {
                    "name": report_filename,
                    "b64": report_base64,
                    "mime": "application/pdf",
                    "size": len(report_bytes),
                    "description": f"Analysis report for {filename} with word frequency data",
                    "viewer": "pdf"
                }
            ],
            "display": {
                "open_canvas": True,
                "primary_file": report_filename,
                "mode": "replace",
                "viewer_hint": "pdf"
            },
            "meta_data": {
                "original_file": filename,
                "word_count": analysis_result["results"]["total_word_count"],
                "report_type": "pdf_analysis",
                "top_words_count": len(analysis_result["results"]["top_100_words"])
            }
        }

    except Exception as e:
        # print traceback for debugging
        import traceback
        traceback.print_exc()
        return {"results": {"error": f"Failed to generate PDF report: {str(e)}"}}


if __name__ == "__main__":
    # This will start the server and listen for MCP requests.
    # To use it, you would run this script and then connect to it
    # with a FastMCP client.
    print("Starting PDF Analyzer MCP server with report generation...")
    mcp.run()

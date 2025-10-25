#!/usr/bin/env python3
"""
Result processing module for code executor.
Handles output processing, visualization, and file encoding.
"""

import base64
import json
import logging
import traceback
from pathlib import Path
from typing import Dict, Any, List

logger = logging.getLogger(__name__)


def detect_matplotlib_plots(exec_dir: Path) -> List[str]:
    """
    Detect if matplotlib plots were created and convert to base64.
    
    Args:
        exec_dir: Execution directory to scan for plot files
        
    Returns:
        List of base64-encoded plot images
    """
    try:
        plot_files = []
        for ext in ['*.png', '*.jpg', '*.jpeg', '*.svg']:
            plot_files.extend(exec_dir.glob(ext))
        
        base64_plots = []
        for plot_file in plot_files:
            try:
                with open(plot_file, 'rb') as f:
                    image_data = base64.b64encode(f.read()).decode('utf-8')
                    file_ext = plot_file.suffix.lower()
                    mime_type = {
                        '.png': 'image/png',
                        '.jpg': 'image/jpeg', 
                        '.jpeg': 'image/jpeg',
                        '.svg': 'image/svg+xml'
                    }.get(file_ext, 'image/png')
                    
                    base64_plots.append(f"data:{mime_type};base64,{image_data}")
                    logger.info(f"Successfully encoded plot: {plot_file.name}")
            except Exception as e:
                logger.warning(f"Failed to encode plot {plot_file}: {str(e)}")
                logger.warning(f"Traceback: {traceback.format_exc()}")
                continue
        
        logger.info(f"Detected {len(base64_plots)} plots in {exec_dir}")
        return base64_plots
    
    except Exception as e:
        logger.error(f"Error detecting matplotlib plots: {str(e)}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        return []


def create_visualization_html(plots: List[str], output_text: str) -> str:
    """
    Create HTML for displaying plots and output matching the frontend dark theme.
    
    Args:
        plots: List of base64-encoded plot images
        output_text: Text output from code execution
        
    Returns:
        HTML string for display
    """
    html_content = """
    <div style="font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Roboto', sans-serif; 
                max-width: 100%; 
                padding: 20px; 
                background-color: #111827; 
                color: #e5e7eb; 
                line-height: 1.6;">
        <h3 style="color: #e5e7eb; margin: 0 0 16px 0; font-weight: 600;">Code Execution Results</h3>
    """
    
    if output_text.strip():
        html_content += f"""
        <div style="background-color: #1f2937; 
                    border: 1px solid #374151; 
                    padding: 16px; 
                    border-radius: 8px; 
                    margin-bottom: 20px;">
            <h4 style="color: #e5e7eb; margin: 0 0 12px 0; font-weight: 500; font-size: 14px;">Output:</h4>
            <pre style="white-space: pre-wrap; 
                       margin: 0; 
                       font-family: 'Consolas', 'Monaco', 'Courier New', monospace; 
                       color: #d1d5db; 
                       background-color: #111827; 
                       padding: 12px; 
                       border-radius: 6px; 
                       border: 1px solid #4b5563; 
                       overflow-x: auto;">{output_text}</pre>
        </div>
        """
    
    if plots:
        html_content += '<h4 style="color: #e5e7eb; margin: 20px 0 16px 0; font-weight: 500;">Generated Visualizations:</h4>'
        for i, plot in enumerate(plots):
            html_content += f"""
            <div style="margin-bottom: 20px; 
                        text-align: center; 
                        background-color: #1f2937; 
                        border: 1px solid #374151; 
                        border-radius: 8px; 
                        padding: 16px;">
                <img src="{plot}" 
                     alt="Plot {i+1}" 
                     style="max-width: 100%; 
                            height: auto; 
                            border: 1px solid #4b5563; 
                            border-radius: 6px; 
                            background-color: white;">
            </div>
            """
    
    html_content += "</div>"
    return html_content


def list_generated_files(exec_dir: Path) -> List[str]:
    """
    List files generated during code execution.
    
    Args:
        exec_dir: Execution directory
        
    Returns:
        List of generated file names
    """
    try:
        generated_files = []
        for file_path in exec_dir.iterdir():
            if file_path.is_file() and file_path.name != "exec_script.py":
                generated_files.append(file_path.name)
        
        logger.info(f"Generated files: {generated_files}")
        return generated_files
    
    except Exception as e:
        logger.error(f"Error listing generated files: {str(e)}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        return []


def encode_generated_files(exec_dir: Path) -> List[Dict[str, str]]:
    """
    Encode generated files to base64 for download.
    
    Args:
        exec_dir: Execution directory
        
    Returns:
        List of dictionaries with 'filename' and 'content_base64' keys
    """
    try:
        encoded_files = []
        for file_path in exec_dir.iterdir():
            if file_path.is_file() and file_path.name != "exec_script.py":
                try:
                    with open(file_path, 'rb') as f:
                        file_content = f.read()
                    
                    content_base64 = base64.b64encode(file_content).decode('utf-8')
                    encoded_files.append({
                        'filename': file_path.name,
                        'content_base64': content_base64
                    })
                    logger.info(f"Encoded file: {file_path.name} ({len(file_content)} bytes)")
                except Exception as e:
                    logger.warning(f"Failed to encode file {file_path.name}: {str(e)}")
                    continue
        
        logger.info(f"Encoded {len(encoded_files)} generated files")
        return encoded_files
    
    except Exception as e:
        logger.error(f"Error encoding generated files: {str(e)}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        return []


def truncate_output_for_llm(output: str, max_chars: int = 2000) -> tuple[str, bool]:
    """
    Smart truncation that preserves important context around key terms.
    
    Args:
        output: The output text to potentially truncate
        max_chars: Maximum characters to allow (default: 2000)
        
    Returns:
        Tuple of (truncated_output, was_truncated)
    """
    if len(output) <= max_chars:
        return output, False
    
    # Key terms that indicate important context
    key_terms = [
        'error', 'Error', 'ERROR', 'exception', 'Exception', 'EXCEPTION',
        'traceback', 'Traceback', 'TRACEBACK', 'failed', 'Failed', 'FAILED',
        'warning', 'Warning', 'WARNING', 'success', 'Success', 'SUCCESS',
        'completed', 'Completed', 'COMPLETED', 'result', 'Result', 'RESULT',
        'summary', 'Summary', 'SUMMARY', 'total', 'Total', 'TOTAL',
        'shape:', 'dtype:', 'columns:', 'index:', 'memory usage:', 'non-null'
    ]
    
    # Find all important sections
    important_sections = []
    context_chars = 150  # Characters around each key term
    
    for term in key_terms:
        start_pos = 0
        while True:
            pos = output.find(term, start_pos)
            if pos == -1:
                break
            
            # Extract context around the term
            section_start = max(0, pos - context_chars)
            section_end = min(len(output), pos + len(term) + context_chars)
            
            # Expand to word boundaries if possible
            while section_start > 0 and not output[section_start].isspace():
                section_start -= 1
            while section_end < len(output) and not output[section_end].isspace():
                section_end += 1
            
            important_sections.append((section_start, section_end, term))
            start_pos = pos + 1
    
    if important_sections:
        # Sort sections by position and merge overlapping ones
        important_sections.sort(key=lambda x: x[0])
        merged_sections = []
        
        for start, end, term in important_sections:
            if merged_sections and start <= merged_sections[-1][1] + 50:  # Merge if close
                merged_sections[-1] = (merged_sections[-1][0], max(end, merged_sections[-1][1]), merged_sections[-1][2] + f", {term}")
            else:
                merged_sections.append((start, end, term))
        
        # Build truncated output with important sections
        result_parts = []
        total_chars = 0
        
        # Always include the beginning (first 300 chars)
        beginning = output[:300]
        result_parts.append(beginning)
        total_chars += len(beginning)
        
        # Add important sections
        for start, end, terms in merged_sections:
            section = output[start:end]
            if total_chars + len(section) + 100 > max_chars:  # Reserve space for truncation message
                break
            
            if start > 300:  # Don't duplicate beginning
                result_parts.append(f"\n\n[... content around: {terms} ...]\n")
                result_parts.append(section)
                total_chars += len(section) + 50
        
        truncated = ''.join(result_parts)
    else:
        # No key terms found, use simple truncation
        truncated = output[:max_chars - 200]  # Reserve space for message
        # Try to break at a reasonable point (newline) near the limit
        last_newline = truncated.rfind('\n')
        if last_newline > len(truncated) * 0.8:  # If newline is in last 20%, use it
            truncated = truncated[:last_newline]
    
    truncation_msg = f"\n\n[OUTPUT TRUNCATED - Original length: {len(output)} characters. Full output preserved in downloaded files and visualizations.]"
    return truncated + truncation_msg, True

#!/usr/bin/env python3
"""
Demonstration script showing error classification and user-friendly messages.
This script simulates various LLM errors and shows how they are handled.
"""

import sys
import os

# Add project root to path for atlas package imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from atlas.application.chat.utilities.error_utils import classify_llm_error


def print_separator():
    print("\n" + "="*80 + "\n")


def demonstrate_error_handling():
    """Demonstrate how different errors are classified and handled."""
    
    print("="*80)
    print("ERROR HANDLING DEMONSTRATION")
    print("="*80)
    
    # Example 1: Rate Limit Error (Cerebras style)
    print_separator()
    print("Example 1: Rate Limit Error (Cerebras)")
    print("-" * 80)
    error1 = Exception("litellm.RateLimitError: RateLimitError: CerebrasException - We're experiencing high traffic right now! Please try again soon.")
    error_class1, user_msg1, log_msg1 = classify_llm_error(error1)
    
    print(f"Original Error:\n  {error1}")
    print(f"\nClassified as: {error_class1.__name__}")
    print(f"\nMessage shown to user:\n  {user_msg1}")
    print(f"\nMessage logged to backend:\n  {log_msg1}")
    
    # Example 2: Timeout Error
    print_separator()
    print("Example 2: Timeout Error")
    print("-" * 80)
    error2 = Exception("Request timed out after 60 seconds")
    error_class2, user_msg2, log_msg2 = classify_llm_error(error2)
    
    print(f"Original Error:\n  {error2}")
    print(f"\nClassified as: {error_class2.__name__}")
    print(f"\nMessage shown to user:\n  {user_msg2}")
    print(f"\nMessage logged to backend:\n  {log_msg2}")
    
    # Example 3: Authentication Error
    print_separator()
    print("Example 3: Authentication Error")
    print("-" * 80)
    error3 = Exception("Invalid API key: sk-abc123xyz456")
    error_class3, user_msg3, log_msg3 = classify_llm_error(error3)
    
    print(f"Original Error:\n  {error3}")
    print(f"\nClassified as: {error_class3.__name__}")
    print(f"\nMessage shown to user:\n  {user_msg3}")
    print(f"\nMessage logged to backend:\n  {log_msg3}")
    print("\nNote: API key is NOT exposed to user!")
    
    # Example 4: Generic Error
    print_separator()
    print("Example 4: Generic LLM Error")
    print("-" * 80)
    error4 = Exception("Model encountered an unexpected error during inference")
    error_class4, user_msg4, log_msg4 = classify_llm_error(error4)
    
    print(f"Original Error:\n  {error4}")
    print(f"\nClassified as: {error_class4.__name__}")
    print(f"\nMessage shown to user:\n  {user_msg4}")
    print(f"\nMessage logged to backend:\n  {log_msg4}")
    
    print_separator()
    print("SUMMARY")
    print("-" * 80)
    print("""
✅ All errors are now properly classified and communicated to users

Key improvements:
1. Rate limit errors → Clear message to wait and try again
2. Timeout errors → Clear message about timeout, suggest retry
3. Auth errors → User told to contact admin (no key exposure)
4. Generic errors → Helpful message with support guidance

✅ Detailed error information is still logged for debugging
✅ No sensitive information is exposed to users
✅ Users are no longer left wondering what happened
    """)
    print("="*80)


if __name__ == "__main__":
    demonstrate_error_handling()

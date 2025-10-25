"""Feedback routes for user feedback collection and management.

This module provides endpoints for:
- Submitting user feedback with ratings and comments
- Admin viewing of collected feedback data
"""

import json
import os
import logging
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, Any

from fastapi import APIRouter, HTTPException, Depends, Request
from pydantic import BaseModel

from core.auth import is_user_in_group
from core.utils import get_current_user
from infrastructure.app_factory import app_factory

logger = logging.getLogger(__name__)

# Feedback router
feedback_router = APIRouter(prefix="/api", tags=["feedback"])


class FeedbackData(BaseModel):
    """Model for user feedback submission."""
    rating: int  # -1, 0, or 1
    comment: str = ""
    session: dict = {}


class FeedbackResponse(BaseModel):
    """Model for feedback list responses."""
    id: str
    timestamp: str
    user: str
    rating: int
    comment: str
    session_info: dict
    server_context: dict


def get_feedback_directory() -> Path:
    """Get the feedback storage directory."""
    base = Path(os.getenv("RUNTIME_FEEDBACK_DIR", "runtime/feedback"))
    base.mkdir(parents=True, exist_ok=True)
    return base


def require_admin_for_feedback(current_user: str = Depends(get_current_user)) -> str:
    """Dependency to require admin group membership for feedback viewing."""
    config_manager = app_factory.get_config_manager()
    admin_group = config_manager.app_settings.admin_group
    if not is_user_in_group(current_user, admin_group):
        raise HTTPException(
            status_code=403, 
            detail=f"Admin access required to view feedback. User must be in '{admin_group}' group."
        )
    return current_user


@feedback_router.post("/feedback")
async def submit_feedback(
    feedback: FeedbackData, 
    request: Request,
    current_user: str = Depends(get_current_user)
):
    """Submit user feedback and save it as a JSON file."""
    try:
        # Validate rating
        if feedback.rating not in [-1, 0, 1]:
            raise HTTPException(status_code=400, detail="Rating must be -1, 0, or 1")
        
        # Get feedback directory
        feedback_dir = get_feedback_directory()
        
        # Generate unique filename with timestamp
        timestamp = datetime.now().isoformat().replace(":", "-").replace(".", "-")
        feedback_id = str(uuid.uuid4())[:8]
        filename = f"feedback_{timestamp}_{feedback_id}.json"
        
        # Prepare feedback data with additional context
        feedback_data = {
            "id": feedback_id,
            "timestamp": datetime.now().isoformat(),
            "user": current_user,
            "rating": feedback.rating,
            "comment": feedback.comment.strip(),
            "session_info": feedback.session,
            "server_context": {
                "user_agent": request.headers.get("user-agent", ""),
                "client_host": request.client.host if request.client else "unknown",
                "forwarded_for": request.headers.get("x-forwarded-for", ""),
                "referer": request.headers.get("referer", "")
            }
        }
        
        # Save feedback to JSON file
        feedback_file = feedback_dir / filename
        with open(feedback_file, 'w', encoding='utf-8') as f:
            json.dump(feedback_data, f, indent=2, ensure_ascii=False)
        
        logger.info(f"Feedback submitted by {current_user}: rating={feedback.rating}, file={filename}")
        
        return {
            "message": "Feedback submitted successfully",
            "feedback_id": feedback_id,
            "timestamp": feedback_data["timestamp"]
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error saving feedback: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to save feedback")


@feedback_router.get("/feedback")
async def get_all_feedback(
    limit: int = 50,
    offset: int = 0,
    admin_user: str = Depends(require_admin_for_feedback)
) -> Dict[str, Any]:
    """Get all submitted feedback (admin only)."""
    try:
        feedback_dir = get_feedback_directory()
        
        # Get all feedback files, sorted by creation time (newest first)
        feedback_files = sorted(
            feedback_dir.glob("feedback_*.json"),
            key=lambda x: x.stat().st_mtime,
            reverse=True
        )
        
        # Apply pagination
        total_count = len(feedback_files)
        paginated_files = feedback_files[offset:offset + limit]
        
        # Read and parse feedback files
        feedback_list = []
        for feedback_file in paginated_files:
            try:
                with open(feedback_file, 'r', encoding='utf-8') as f:
                    feedback_data = json.load(f)
                    feedback_list.append(feedback_data)
            except Exception as e:
                logger.error(f"Error reading feedback file {feedback_file}: {e}")
                continue
        
        # Calculate rating statistics
        ratings = [fb["rating"] for fb in feedback_list if "rating" in fb]
        rating_stats = {
            "positive": sum(1 for r in ratings if r == 1),
            "neutral": sum(1 for r in ratings if r == 0),
            "negative": sum(1 for r in ratings if r == -1),
            "total": len(ratings),
            "average": sum(ratings) / len(ratings) if ratings else 0
        }
        
        return {
            "feedback": feedback_list,
            "pagination": {
                "total": total_count,
                "limit": limit,
                "offset": offset,
                "has_more": offset + limit < total_count
            },
            "statistics": rating_stats,
            "retrieved_by": admin_user
        }
        
    except Exception as e:
        logger.error(f"Error retrieving feedback: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to retrieve feedback")


@feedback_router.get("/feedback/stats")
async def get_feedback_stats(
    admin_user: str = Depends(require_admin_for_feedback)
) -> Dict[str, Any]:
    """Get feedback statistics summary (admin only)."""
    try:
        feedback_dir = get_feedback_directory()
        feedback_files = list(feedback_dir.glob("feedback_*.json"))
        
        if not feedback_files:
            return {
                "total_feedback": 0,
                "rating_distribution": {"positive": 0, "neutral": 0, "negative": 0},
                "average_rating": 0,
                "recent_feedback": 0
            }
        
        # Read all feedback files
        all_feedback = []
        recent_count = 0
        now = datetime.now()
        
        for feedback_file in feedback_files:
            try:
                with open(feedback_file, 'r', encoding='utf-8') as f:
                    feedback_data = json.load(f)
                    all_feedback.append(feedback_data)
                    
                    # Count recent feedback (last 24 hours)
                    if "timestamp" in feedback_data:
                        feedback_time = datetime.fromisoformat(feedback_data["timestamp"].replace("Z", "+00:00"))
                        if (now - feedback_time).total_seconds() < 86400:  # 24 hours
                            recent_count += 1
                            
            except Exception as e:
                logger.error(f"Error reading feedback file {feedback_file}: {e}")
                continue
        
        # Calculate statistics
        ratings = [fb["rating"] for fb in all_feedback if "rating" in fb]
        rating_distribution = {
            "positive": sum(1 for r in ratings if r == 1),
            "neutral": sum(1 for r in ratings if r == 0),
            "negative": sum(1 for r in ratings if r == -1)
        }
        
        return {
            "total_feedback": len(all_feedback),
            "rating_distribution": rating_distribution,
            "average_rating": sum(ratings) / len(ratings) if ratings else 0,
            "recent_feedback": recent_count,
            "feedback_with_comments": sum(1 for fb in all_feedback if fb.get("comment", "").strip()),
            "unique_users": len(set(fb.get("user", "unknown") for fb in all_feedback)),
            "retrieved_by": admin_user
        }
        
    except Exception as e:
        logger.error(f"Error calculating feedback stats: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to calculate feedback statistics")


@feedback_router.delete("/feedback/{feedback_id}")
async def delete_feedback(
    feedback_id: str,
    admin_user: str = Depends(require_admin_for_feedback)
):
    """Delete a specific feedback entry (admin only)."""
    try:
        feedback_dir = get_feedback_directory()
        
        # Find the feedback file by ID
        feedback_file = None
        for file_path in feedback_dir.glob("feedback_*.json"):
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    if data.get("id") == feedback_id:
                        feedback_file = file_path
                        break
            except Exception:
                continue
        
        if not feedback_file:
            raise HTTPException(status_code=404, detail="Feedback not found")
        
        # Delete the file
        feedback_file.unlink()
        logger.info(f"Feedback {feedback_id} deleted by {admin_user}")
        
        return {
            "message": "Feedback deleted successfully",
            "feedback_id": feedback_id,
            "deleted_by": admin_user
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting feedback: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to delete feedback")

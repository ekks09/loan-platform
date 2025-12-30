"""
Admin views for user verification.
"""
from __future__ import annotations

import logging
from django.shortcuts import render
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from django.contrib.admin.views.decorators import staff_member_required
from django.views.decorators.csrf import csrf_protect
from django.utils import timezone

from .models import User

logger = logging.getLogger(__name__)


@staff_member_required
def verification_dashboard(request):
    """
    Admin dashboard for reviewing user verification documents.
    """
    # Get filter from query params
    status_filter = request.GET.get('status', 'pending')
    
    # Query users based on filter
    if status_filter == 'all':
        users = User.objects.exclude(
            id_front_url__isnull=True
        ).exclude(
            id_front_url=''
        ).order_by('-created_at')[:50]
    else:
        users = User.objects.filter(
            verification_status=status_filter
        ).exclude(
            id_front_url__isnull=True
        ).exclude(
            id_front_url=''
        ).order_by('-created_at')[:50]

    # Count by status
    counts = {
        'pending': User.objects.filter(verification_status='pending').exclude(id_front_url__isnull=True).exclude(id_front_url='').count(),
        'verified': User.objects.filter(verification_status='verified').count(),
        'rejected': User.objects.filter(verification_status='rejected').count(),
    }

    return render(request, 'admin/verification_dashboard.html', {
        'users': users,
        'current_filter': status_filter,
        'counts': counts,
    })


@staff_member_required
@csrf_protect
@require_http_methods(["POST"])
def verify_user(request, user_id):
    """
    Approve or reject a user's verification.
    """
    import json

    try:
        data = json.loads(request.body)
        action = data.get('action')  # 'approve' or 'reject'
        notes = data.get('notes', '')

        if action not in ['approve', 'reject']:
            return JsonResponse({'error': 'Invalid action. Must be "approve" or "reject".'}, status=400)

        try:
            user = User.objects.get(pk=user_id)
        except User.DoesNotExist:
            return JsonResponse({'error': 'User not found.'}, status=404)

        # Update user verification status
        if action == 'approve':
            user.verification_status = User.VerificationStatus.VERIFIED
            user.verified_at = timezone.now()
            user.verified_by = request.user
            user.verification_notes = notes or "Verification approved."
        else:
            user.verification_status = User.VerificationStatus.REJECTED
            user.verification_notes = notes or "Verification rejected. Please upload clearer photos."

        user.save()

        logger.info(f"User {user.phone} verification {action}d by {request.user.phone}")

        return JsonResponse({
            'success': True,
            'message': f'User verification {action}d successfully.',
            'new_status': user.verification_status,
        })

    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON data.'}, status=400)
    except Exception as e:
        logger.exception(f"Verification error: {e}")
        return JsonResponse({'error': str(e)}, status=500)


@staff_member_required
def verification_api(request):
    """
    API endpoint to get pending verifications as JSON.
    """
    status_filter = request.GET.get('status', 'pending')
    
    users = User.objects.filter(
        verification_status=status_filter
    ).exclude(
        id_front_url__isnull=True
    ).exclude(
        id_front_url=''
    ).order_by('-created_at')[:50]

    data = [{
        'id': user.id,
        'phone': user.phone,
        'national_id': user.national_id,
        'id_front_url': user.id_front_url,
        'id_back_url': user.id_back_url,
        'selfie_url': user.selfie_url,
        'verification_status': user.verification_status,
        'created_at': user.created_at.isoformat(),
    } for user in users]

    return JsonResponse({'users': data})

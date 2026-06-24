#!/bin/bash
# Test script for PR #648: Native PDF document support (base64 passthrough to LLM)
#
# Test plan:
# - Verify ModelConfig accepts supports_pdf and defaults to False
# - Verify /api/config exposes supports_pdf per model
# - E2E: a supports_pdf model stores pdf_b64 and emits a `file` content block
#   on the last user message, excluded from the text manifest
# - E2E: a non-pdf model leaves the user message as a plain string
# - Verify size and page-count guards demote oversized/over-long PDFs
# - Run the PDF + vision unit test suites

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

PASSED=0
FAILED=0
SKIPPED=0

print_header() {
    echo ""
    echo "=========================================="
    echo "$1"
    echo "=========================================="
}

print_result() {
    if [ $1 -eq 0 ]; then
        echo -e "${GREEN}PASSED${NC}: $2"
        PASSED=$((PASSED + 1))
    else
        echo -e "${RED}FAILED${NC}: $2"
        FAILED=$((FAILED + 1))
    fi
}

# Activate venv
if [ -f "$PROJECT_ROOT/.venv/bin/activate" ]; then
    source "$PROJECT_ROOT/.venv/bin/activate"
fi

cd "$PROJECT_ROOT"

# ==========================================
# Check 1: ModelConfig accepts supports_pdf
# ==========================================
print_header "Check 1: ModelConfig accepts supports_pdf"

python3 -c "
from atlas.modules.config.config_manager import ModelConfig
assert ModelConfig(model_name='m', model_url='http://x').supports_pdf is False
assert ModelConfig(model_name='m', model_url='http://x', supports_pdf=True).supports_pdf is True
print('supports_pdf parsed and defaults correctly')
"
print_result $? "ModelConfig.supports_pdf defaults False and can be set True"

# ==========================================
# Check 2: config_routes exposes supports_pdf
# ==========================================
print_header "Check 2: config_routes exposes supports_pdf"

grep -q 'supports_pdf' "$PROJECT_ROOT/atlas/routes/config_routes.py"
print_result $? "config_routes.py includes supports_pdf in model info"

# ==========================================
# Check 3: E2E - PDF stored and emitted as a document content block
# ==========================================
print_header "Check 3: E2E - PDF becomes a file content block, excluded from manifest"

python3 -c "
import asyncio, base64
from io import BytesIO
import pypdf

from atlas.application.chat.utilities.file_processor import handle_session_files
from atlas.application.chat.preprocessors.message_builder import MessageBuilder
from atlas.domain.messages.models import Message, MessageRole
from atlas.domain.sessions.models import Session
from atlas.modules.file_storage.manager import FileManager
from atlas.modules.file_storage.mock_s3_client import MockS3StorageClient
import uuid

def make_pdf(pages=1):
    w = pypdf.PdfWriter()
    for _ in range(pages):
        w.add_blank_page(width=72, height=72)
    buf = BytesIO(); w.write(buf)
    return base64.b64encode(buf.getvalue()).decode()

async def main():
    fm = FileManager(s3_client=MockS3StorageClient())
    b64 = make_pdf()
    session = Session(id=uuid.uuid4(), user_email='u@example.com')
    session.history.add_message(Message(role=MessageRole.USER, content='Summarize this PDF'))

    session.context = await handle_session_files(
        session_context=session.context,
        user_email='u@example.com',
        files_map={'doc.pdf': {'content': b64, 'extractMode': 'full'}},
        file_manager=fm,
        model_supports_pdf=True,
    )
    ref = session.context['files']['doc.pdf']
    assert ref.get('pdf_b64') == b64, 'pdf_b64 not stored'
    # The native PDF is excluded from the manifest below; whether a text
    # fallback is also recorded depends on extraction being enabled in this
    # environment, so we do not assert on extracted_content here.

    messages = await MessageBuilder().build_messages(
        session=session, include_system_prompt=False, include_files_manifest=True,
        model_supports_pdf=True,
    )
    last_user = [m for m in messages if m.get('role') == 'user'][-1]
    blocks = last_user['content']
    assert isinstance(blocks, list), 'user content should be multimodal list'
    file_blocks = [b for b in blocks if b.get('type') == 'file']
    assert len(file_blocks) == 1, 'expected one file block'
    assert file_blocks[0]['file']['file_data'].startswith('data:application/pdf;base64,'), 'wrong data URI'

    for sm in [m for m in messages if m.get('role') == 'system']:
        assert 'doc.pdf' not in sm.get('content', ''), 'PDF should be excluded from manifest'
    print('PDF emitted as file content block and excluded from manifest')

asyncio.run(main())
"
print_result $? "Native PDF emitted as file block and excluded from manifest"

# ==========================================
# Check 4: Non-PDF model leaves message as plain string
# ==========================================
print_header "Check 4: Non-PDF model uses text/manifest path"

python3 -c "
import asyncio, base64
from io import BytesIO
import pypdf, uuid
from atlas.application.chat.utilities.file_processor import handle_session_files
from atlas.application.chat.preprocessors.message_builder import MessageBuilder
from atlas.domain.messages.models import Message, MessageRole
from atlas.domain.sessions.models import Session
from atlas.modules.file_storage.manager import FileManager
from atlas.modules.file_storage.mock_s3_client import MockS3StorageClient

def make_pdf():
    w = pypdf.PdfWriter(); w.add_blank_page(width=72, height=72)
    buf = BytesIO(); w.write(buf)
    return base64.b64encode(buf.getvalue()).decode()

async def main():
    fm = FileManager(s3_client=MockS3StorageClient())
    session = Session(id=uuid.uuid4(), user_email='u@example.com')
    session.history.add_message(Message(role=MessageRole.USER, content='Hi'))
    session.context = await handle_session_files(
        session_context=session.context, user_email='u@example.com',
        files_map={'doc.pdf': {'content': make_pdf(), 'extractMode': 'none'}},
        file_manager=fm, model_supports_pdf=False,
    )
    assert 'pdf_b64' not in session.context['files']['doc.pdf']
    messages = await MessageBuilder().build_messages(
        session=session, include_system_prompt=False, model_supports_pdf=False,
    )
    last_user = [m for m in messages if m.get('role') == 'user'][-1]
    assert isinstance(last_user['content'], str), 'content must stay a string without supports_pdf'
    print('Non-PDF model keeps plain-string user message')

asyncio.run(main())
"
print_result $? "Non-PDF model leaves user message as plain string"

# ==========================================
# Check 5: Size and page guards demote PDFs
# ==========================================
print_header "Check 5: Size and page guards"

python3 -c "
import asyncio, base64
from io import BytesIO
import pypdf, uuid
from atlas.application.chat.utilities.file_processor import (
    handle_session_files, _MAX_PDF_B64_BYTES, _MAX_PDF_PAGES,
)
from atlas.modules.file_storage.manager import FileManager
from atlas.modules.file_storage.mock_s3_client import MockS3StorageClient

async def main():
    fm = FileManager(s3_client=MockS3StorageClient())
    # Oversized
    oversized = base64.b64encode(b'%PDF-' + b'x' * _MAX_PDF_B64_BYTES).decode()
    ctx = await handle_session_files(
        session_context={}, user_email='u@example.com',
        files_map={'huge.pdf': {'content': oversized, 'extractMode': 'none'}},
        file_manager=fm, model_supports_pdf=True,
    )
    assert 'pdf_b64' not in ctx['files']['huge.pdf'], 'oversized PDF must not be stored'

    # Over page limit
    w = pypdf.PdfWriter()
    for _ in range(_MAX_PDF_PAGES + 1):
        w.add_blank_page(width=72, height=72)
    buf = BytesIO(); w.write(buf)
    long_b64 = base64.b64encode(buf.getvalue()).decode()
    ctx2 = await handle_session_files(
        session_context={}, user_email='u@example.com',
        files_map={'long.pdf': {'content': long_b64, 'extractMode': 'none'}},
        file_manager=fm, model_supports_pdf=True,
    )
    assert 'pdf_b64' not in ctx2['files']['long.pdf'], 'over-length PDF must not be stored'
    print('Size and page guards demote PDFs to fallback path')

asyncio.run(main())
"
print_result $? "Oversized and over-length PDFs fall back to text path"

# ==========================================
# Check 6: Unit test suites
# ==========================================
print_header "Check 6: PDF and vision unit tests"

python3 -m pytest atlas/tests/test_pdf_document_support.py atlas/tests/test_vision_image_support.py -q
print_result $? "PDF and vision unit tests pass"

# ==========================================
# Check 7: Docs and changelog
# ==========================================
print_header "Check 7: Documentation and changelog"

test -f "$PROJECT_ROOT/docs/developer/design-notes/pdf-document-support-2026-06-12.md"
print_result $? "Developer doc for PDF support exists"

grep -q "648" "$PROJECT_ROOT/CHANGELOG.md"
print_result $? "CHANGELOG.md has entry for PR #648"

# ==========================================
# Summary
# ==========================================
print_header "SUMMARY"
echo -e "Passed: ${GREEN}$PASSED${NC}"
echo -e "Failed: ${RED}$FAILED${NC}"
echo -e "Skipped: ${YELLOW}$SKIPPED${NC}"
echo ""

if [ $FAILED -gt 0 ]; then
    echo -e "${RED}PR #648 validation FAILED${NC}"
    exit 1
else
    echo -e "${GREEN}PR #648 validation PASSED${NC}"
    exit 0
fi

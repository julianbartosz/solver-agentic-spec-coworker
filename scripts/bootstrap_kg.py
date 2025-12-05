#!/usr/bin/env python
"""
Bootstrap the Knowledge Graph with initial workflow templates.

This script migrates the v1 hardcoded templates into the KG database,
making them queryable via GraphRAG.

Usage:
    python scripts/bootstrap_kg.py
    
    # With custom database URL:
    DATABASE_URL=postgresql://... python scripts/bootstrap_kg.py
    
    # Dry run (show what would be inserted):
    python scripts/bootstrap_kg.py --dry-run

V2 Architecture:
- Hardcoded templates removed from align_task_with_kg.py (ADR-0004)
- Templates now live exclusively in the KG database
- This script seeds the initial templates for common providers
"""
import argparse
import sys
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


# Templates migrated from v1 _LEGACY_WORKFLOW_TEMPLATES
BOOTSTRAP_TEMPLATES = [
    # -------------------------------------------------------------------------
    # Stripe Payment Intents Templates
    # -------------------------------------------------------------------------
    {
        "provider_code": "stripe",
        "template_id": "stripe_payment_intent_v1",
        "name": "Stripe Create Payment Intent",
        "description": "Standard flow for creating a Stripe PaymentIntent",
        "steps": [
            {"key": "start", "type": "start", "label": "Start"},
            {"key": "validate_input", "type": "validation", "label": "Validate Input",
             "description": "Validate amount, currency, and payment method types"},
            {"key": "call_create_intent", "type": "api_call", "label": "Create PaymentIntent",
             "description": "POST to /v1/payment_intents"},
            {"key": "transform_response", "type": "transform", "label": "Transform Response",
             "description": "Extract id, client_secret, and status"},
            {"key": "end", "type": "end", "label": "Return Result"},
        ],
        "tags": ["payment", "create", "intent"],
    },
    {
        "provider_code": "stripe",
        "template_id": "stripe_confirm_intent_v1",
        "name": "Stripe Confirm Payment Intent",
        "description": "Flow for confirming a PaymentIntent with payment method",
        "steps": [
            {"key": "start", "type": "start", "label": "Start"},
            {"key": "validate_input", "type": "validation", "label": "Validate Input",
             "description": "Validate payment_intent_id and payment_method"},
            {"key": "call_confirm", "type": "api_call", "label": "Confirm PaymentIntent",
             "description": "POST to /v1/payment_intents/{id}/confirm"},
            {"key": "transform_response", "type": "transform", "label": "Transform Response",
             "description": "Extract status and next_action if required"},
            {"key": "end", "type": "end", "label": "Return Result"},
        ],
        "tags": ["payment", "confirm", "intent"],
    },
    {
        "provider_code": "stripe",
        "template_id": "stripe_get_intent_v1",
        "name": "Stripe Get Payment Intent",
        "description": "Retrieve an existing PaymentIntent by ID",
        "steps": [
            {"key": "start", "type": "start", "label": "Start"},
            {"key": "validate_input", "type": "validation", "label": "Validate Input",
             "description": "Ensure payment_intent_id is provided"},
            {"key": "call_get_intent", "type": "api_call", "label": "Get PaymentIntent",
             "description": "GET /v1/payment_intents/{id}"},
            {"key": "transform_response", "type": "transform", "label": "Transform Response",
             "description": "Return full PaymentIntent object"},
            {"key": "end", "type": "end", "label": "Return Result"},
        ],
        "tags": ["payment", "get", "intent"],
    },
    {
        "provider_code": "stripe",
        "template_id": "stripe_cancel_intent_v1",
        "name": "Stripe Cancel Payment Intent",
        "description": "Cancel a PaymentIntent",
        "steps": [
            {"key": "start", "type": "start", "label": "Start"},
            {"key": "validate_input", "type": "validation", "label": "Validate Input",
             "description": "Validate payment_intent_id and cancellation_reason"},
            {"key": "call_cancel", "type": "api_call", "label": "Cancel PaymentIntent",
             "description": "POST to /v1/payment_intents/{id}/cancel"},
            {"key": "transform_response", "type": "transform", "label": "Transform Response",
             "description": "Confirm cancellation status"},
            {"key": "end", "type": "end", "label": "Return Result"},
        ],
        "tags": ["payment", "cancel", "intent"],
    },
    {
        "provider_code": "stripe",
        "template_id": "stripe_checkout_v1",
        "name": "Stripe Checkout Session Creation",
        "description": "Standard flow for creating a Stripe checkout session",
        "steps": [
            {"key": "start", "type": "start", "label": "Start"},
            {"key": "validate_input", "type": "validation", "label": "Validate Input",
             "description": "Validate amount, currency, and URLs"},
            {"key": "call_create_session", "type": "api_call", "label": "Call Create Session",
             "description": "POST to /v1/checkout/sessions"},
            {"key": "transform_response", "type": "transform", "label": "Transform Response",
             "description": "Extract session_id and checkout_url"},
            {"key": "end", "type": "end", "label": "Return Result"},
        ],
        "tags": ["checkout", "session", "create"],
    },
    # -------------------------------------------------------------------------
    # Mock Payments Templates (for testing)
    # -------------------------------------------------------------------------
    {
        "provider_code": "mock_payments",
        "template_id": "mock_checkout_v1",
        "name": "Mock Payments Checkout Session",
        "description": "Standard checkout flow for mock payment provider",
        "steps": [
            {"key": "start", "type": "start", "label": "Start"},
            {"key": "validate_input", "type": "validation", "label": "Validate Input"},
            {"key": "call_create_session", "type": "api_call", "label": "API Call"},
            {"key": "transform_response", "type": "transform", "label": "Transform"},
            {"key": "end", "type": "end", "label": "End"},
        ],
        "tags": ["checkout", "session", "mock"],
    },
    {
        "provider_code": "mock_payments",
        "template_id": "mock_get_session_v1",
        "name": "Mock Payments Get Checkout Session",
        "description": "Retrieve existing checkout session by ID from mock provider",
        "steps": [
            {"key": "start", "type": "start", "label": "Start"},
            {"key": "validate_input", "type": "validation", "label": "Validate Session ID",
             "description": "Ensure session_id is provided and valid format"},
            {"key": "call_get_session", "type": "api_call", "label": "GET Session",
             "description": "GET /checkout/sessions/{session_id}"},
            {"key": "transform_response", "type": "transform", "label": "Transform Response",
             "description": "Extract and normalize session details"},
            {"key": "end", "type": "end", "label": "Return Session"},
        ],
        "tags": ["checkout", "session", "get", "mock"],
    },
    # -------------------------------------------------------------------------
    # Generic Templates (for any provider)
    # -------------------------------------------------------------------------
    {
        "provider_code": "generic",
        "template_id": "generic_create_resource_v1",
        "name": "Generic Create Resource",
        "description": "Standard POST flow for creating a resource",
        "steps": [
            {"key": "start", "type": "start", "label": "Start"},
            {"key": "validate_input", "type": "validation", "label": "Validate Input",
             "description": "Validate request body fields"},
            {"key": "call_create", "type": "api_call", "label": "Create Resource",
             "description": "POST to resource endpoint"},
            {"key": "transform_response", "type": "transform", "label": "Transform Response",
             "description": "Extract created resource with ID"},
            {"key": "end", "type": "end", "label": "Return Result"},
        ],
        "tags": ["create", "post", "generic"],
    },
    {
        "provider_code": "generic",
        "template_id": "generic_get_resource_v1",
        "name": "Generic Get Resource",
        "description": "Standard GET flow for fetching a single resource",
        "steps": [
            {"key": "start", "type": "start", "label": "Start"},
            {"key": "validate_id", "type": "validation", "label": "Validate Resource ID",
             "description": "Ensure ID is provided and valid"},
            {"key": "call_get", "type": "api_call", "label": "Fetch Resource",
             "description": "GET resource by ID"},
            {"key": "handle_response", "type": "transform", "label": "Handle Response",
             "description": "Return resource or 404"},
            {"key": "end", "type": "end", "label": "Return Result"},
        ],
        "tags": ["get", "fetch", "generic"],
    },
    {
        "provider_code": "generic",
        "template_id": "generic_list_resources_v1",
        "name": "Generic List Resources",
        "description": "Standard GET flow for listing resources with pagination",
        "steps": [
            {"key": "start", "type": "start", "label": "Start"},
            {"key": "build_query", "type": "validation", "label": "Build Query",
             "description": "Construct filter and pagination parameters"},
            {"key": "call_list", "type": "api_call", "label": "List Resources",
             "description": "GET collection endpoint"},
            {"key": "paginate_response", "type": "transform", "label": "Handle Pagination",
             "description": "Extract items and pagination metadata"},
            {"key": "end", "type": "end", "label": "Return Result"},
        ],
        "tags": ["list", "get", "pagination", "generic"],
    },
    {
        "provider_code": "generic",
        "template_id": "generic_update_resource_v1",
        "name": "Generic Update Resource",
        "description": "Standard PUT/PATCH flow for updating a resource",
        "steps": [
            {"key": "start", "type": "start", "label": "Start"},
            {"key": "validate_input", "type": "validation", "label": "Validate Input",
             "description": "Validate ID and update fields"},
            {"key": "call_update", "type": "api_call", "label": "Update Resource",
             "description": "PUT/PATCH to resource endpoint"},
            {"key": "transform_response", "type": "transform", "label": "Transform Response",
             "description": "Return updated resource"},
            {"key": "end", "type": "end", "label": "Return Result"},
        ],
        "tags": ["update", "put", "patch", "generic"],
    },
    {
        "provider_code": "generic",
        "template_id": "generic_delete_resource_v1",
        "name": "Generic Delete Resource",
        "description": "Standard DELETE flow for removing a resource",
        "steps": [
            {"key": "start", "type": "start", "label": "Start"},
            {"key": "validate_id", "type": "validation", "label": "Validate Resource ID",
             "description": "Ensure ID is provided"},
            {"key": "call_delete", "type": "api_call", "label": "Delete Resource",
             "description": "DELETE resource by ID"},
            {"key": "confirm_deleted", "type": "transform", "label": "Confirm Deletion",
             "description": "Return deletion confirmation"},
            {"key": "end", "type": "end", "label": "Return Result"},
        ],
        "tags": ["delete", "remove", "generic"],
    },
]


def main():
    parser = argparse.ArgumentParser(
        description="Bootstrap the Knowledge Graph with initial workflow templates"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be inserted without actually inserting",
    )
    args = parser.parse_args()

    print("=== KG Bootstrap Script (V2) ===")
    print()

    if args.dry_run:
        print("DRY RUN MODE - No changes will be made\n")
        for template in BOOTSTRAP_TEMPLATES:
            print(f"  Would insert: {template['provider_code']}/{template['template_id']}")
            print(f"    Name: {template['name']}")
            print(f"    Steps: {len(template['steps'])}")
            print(f"    Tags: {template.get('tags', [])}")
            print()
        print(f"Total: {len(BOOTSTRAP_TEMPLATES)} templates")
        return

    # Import here to allow --dry-run without database
    try:
        from integration_coworker.persistence.db import init_schema, get_engine_type
        from integration_coworker.kg import persist_workflow_template
    except ImportError as e:
        print(f"ERROR: Failed to import modules: {e}")
        print("Make sure you're running from the project root with proper PYTHONPATH")
        sys.exit(1)

    # Initialize schema
    print(f"Database engine: {get_engine_type()}")
    print("Initializing schema...")
    init_schema()

    # Insert templates
    print(f"\nInserting {len(BOOTSTRAP_TEMPLATES)} templates...")
    success_count = 0
    error_count = 0

    for template in BOOTSTRAP_TEMPLATES:
        try:
            persist_workflow_template(
                provider_code=template["provider_code"],
                template_id=template["template_id"],
                name=template["name"],
                description=template["description"],
                steps=template["steps"],
                tags=template.get("tags", []),
            )
            print(f"  ✓ {template['provider_code']}/{template['template_id']}")
            success_count += 1
        except Exception as e:
            print(f"  ✗ {template['provider_code']}/{template['template_id']}: {e}")
            error_count += 1

    print()
    print(f"Bootstrap complete: {success_count} inserted, {error_count} errors")

    if error_count > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()

from fastapi import APIRouter

router = APIRouter()


# BEGIN AUTO-GENERATED INTEGRATION ROUTES
from workflows.stripe_create_checkout_session import create_checkout_session_flow as flow_module

router.include_router(
    flow_module.router if hasattr(flow_module, 'router') else APIRouter(),
    prefix="/integrations/stripe/create_checkout_session",
    tags=["stripe"],
)
# END AUTO-GENERATED INTEGRATION ROUTES

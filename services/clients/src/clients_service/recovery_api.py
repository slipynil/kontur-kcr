from uuid import UUID

from fastapi import APIRouter, Request

from clients_service.import_models import Quarter
from clients_service.recovery_application import execute_recovery
from clients_service.recovery_models import RecoveryCommand

router = APIRouter(prefix="/internal/v1", tags=["Recovery"])


@router.post("/import/{batch_id}/rollback", operation_id="rollbackImport")
def rollback(batch_id: UUID, command: RecoveryCommand, request: Request):
    return execute_recovery(request.app.state.engine, command, action="ROLLBACK", batch_id=batch_id)


@router.delete("/clients/{kcr_code}", operation_id="deleteClient")
def delete_client(kcr_code: str, command: RecoveryCommand, request: Request):
    return execute_recovery(
        request.app.state.engine, command, action="DELETE_CLIENT", code=kcr_code
    )


@router.delete("/clients/{kcr_code}/snapshots/{quarter}", operation_id="deleteClientQuarter")
def delete_quarter(kcr_code: str, quarter: Quarter, command: RecoveryCommand, request: Request):
    return execute_recovery(
        request.app.state.engine, command, action="DELETE_QUARTER", code=kcr_code, quarter=quarter
    )

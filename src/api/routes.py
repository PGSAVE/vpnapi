from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from typing import Optional

from src.api.middleware import auth_dependency
from src.config import PANEL_SUB_URL
from src.models.package import list_packages
from src.models.subscription import list_subscriptions, get_subscription_for_client
from src.services.subscription_service import create_sub, delete_sub, renew_sub, APIError

router = APIRouter(prefix="/vpnapi", dependencies=[Depends(auth_dependency)])


class CreateSubscriptionBody(BaseModel):
    packageId: int = Field(..., description="ID тарифного пакета")
    userId: Optional[str] = Field(None, description="Идентификатор пользователя (опционально). Если не указан, генерируется автоматически")


@router.get(
    "/packages",
    summary="Список пакетов",
    description="Возвращает список всех активных тарифных пакетов.",
    responses={401: {"description": "Неверный или неактивный токен"}},
)
def get_packages(ct=Depends(auth_dependency)):
    pkgs = list_packages(active_only=True)
    for p in pkgs:
        p.pop("groups", None)
    return pkgs


@router.get(
    "/balance",
    summary="Баланс клиента",
    description="Возвращает текущий баланс и имя клиента.",
    responses={401: {"description": "Неверный или неактивный токен"}},
)
def get_balance(ct=Depends(auth_dependency)):
    return {"balance": ct["balance"], "name": ct["name"]}


@router.post(
    "/subscriptions",
    status_code=201,
    summary="Создать подписку",
    description="Создаёт новую VPN-подписку по указанному пакету. "
    "Стоимость списывается с баланса клиента. "
    "Возвращает данные подписки и ссылку на конфигурацию.",
    responses={
        201: {"description": "Подписка успешно создана"},
        402: {"description": "Недостаточно средств на балансе"},
        404: {"description": "Пакет не найден"},
        502: {"description": "Ошибка панели управления"},
    },
)
def post_subscription(body: CreateSubscriptionBody, ct=Depends(auth_dependency)):
    try:
        return create_sub(ct, body.packageId, body.userId)
    except APIError as e:
        raise HTTPException(e.status_code, str(e))


@router.get(
    "/subscriptions",
    summary="Список подписок",
    description="Возвращает все подписки текущего клиента.",
    responses={401: {"description": "Неверный или неактивный токен"}},
)
def get_subscriptions(ct=Depends(auth_dependency)):
    return list_subscriptions(ct["id"])


@router.get(
    "/subscriptions/{sub_id}",
    summary="Детали подписки",
    description="Возвращает подробную информацию о конкретной подписке, включая ссылку на конфигурацию.",
    responses={
        404: {"description": "Подписка не найдена"},
    },
)
def get_subscription(sub_id: int, ct=Depends(auth_dependency)):
    sub = get_subscription_for_client(sub_id, ct["id"])
    if not sub:
        raise HTTPException(404, "Not found")
    sub["sub_link"] = f"{PANEL_SUB_URL}/api/files/{sub['panel_subscription_token']}" if sub.get("panel_subscription_token") else None
    return sub


@router.post(
    "/subscriptions/{sub_id}/renew",
    summary="Продлить подписку",
    description="Продлевает существующую подписку на срок пакета. "
    "Стоимость списывается с баланса. "
    "Срок добавляется к текущей дате истечения (или от текущего момента, если подписка просрочена).",
    responses={
        200: {"description": "Подписка успешно продлена"},
        400: {"description": "Невозможно продлить удалённую подписку"},
        402: {"description": "Недостаточно средств на балансе"},
        404: {"description": "Подписка не найдена"},
        502: {"description": "Ошибка панели управления"},
    },
)
def renew_subscription(sub_id: int, ct=Depends(auth_dependency)):
    try:
        sub = renew_sub(ct, sub_id)
        return {"success": True, "subscription": sub}
    except APIError as e:
        raise HTTPException(e.status_code, str(e))


@router.delete(
    "/subscriptions/{sub_id}",
    summary="Удалить подписку",
    description="Удаляет подписку без возможности восстановления. Пользователь на панели также удаляется.",
    responses={
        200: {"description": "Подписка успешно удалена"},
        400: {"description": "Подписка уже удалена"},
        404: {"description": "Подписка не найдена"},
        502: {"description": "Ошибка панели управления"},
    },
)
def del_subscription(sub_id: int, ct=Depends(auth_dependency)):
    try:
        sub = delete_sub(sub_id, ct["id"])
        return {"success": True, "subscription": sub}
    except APIError as e:
        raise HTTPException(e.status_code, str(e))

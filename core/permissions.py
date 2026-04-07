from rest_framework.permissions import BasePermission


ROLE_ALIASES = {
    "doctor": "doctor",
    "medico": "doctor",
    "admin": "doctor",
    "fisioterapeuta": "fisioterapeuta",
    "aux_fisioterapia": "aux_fisioterapia",
    "auxiliar_fisioterapia": "aux_fisioterapia",
    "subfisioterapeuta": "aux_fisioterapia",
    "sub_fisioterapeuta": "aux_fisioterapia",
    "recepcionista": "recepcionista",
    "recepcion": "recepcionista",
}


def normalize_role(value, fallback=None):
    role = str(value or "").strip().lower()
    if not role:
        return fallback
    return ROLE_ALIASES.get(role, fallback or role)


def get_user_role(user):
    if not user or not user.is_authenticated:
        return None

    staff_profile = getattr(user, "staff_profile", None)
    if staff_profile and getattr(staff_profile, "rol", None):
        return normalize_role(staff_profile.rol)

    if getattr(user, "is_superuser", False):
        return "doctor"

    if getattr(user, "is_staff", False):
        return "doctor"

    return None


class IsAdminUserStrict(BasePermission):
    message = "You do not have permission to perform this action."

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False

        role = get_user_role(user)

        return bool(
            user.is_superuser
            or role in {"doctor", "fisioterapeuta"}
        )
from django.contrib import admin
from .models import Clinica, HorarioDisponible, Servicio, Paciente, Comentario, Cita

admin.site.register(Clinica)
admin.site.register(HorarioDisponible)
admin.site.register(Servicio)
admin.site.register(Paciente)
admin.site.register(Comentario)
admin.site.register(Cita)

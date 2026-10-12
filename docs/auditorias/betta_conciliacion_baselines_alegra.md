# Conciliación de baselines Alegra → BettaApp

- Modo: `DRY-RUN`.
- Contactos consultados: 168; páginas: 6; cobertura: `complete`.
- Baselines existentes: 1.
- Baselines confirmables: 0.
- Revisión manual: 156.
- Contactos vinculados no recuperados: 0.
- Baselines inicializados: 0.
- Errores de consulta: 0; IDs duplicados observados: 0.

## Diagnóstico agregado de REVIEW

- Clientes afectados por revisión: 156.
- Categorías excluyentes: {'FIELD_DIFFERENCES': 155, 'MISSING_PROTECTED_FIELDS': 1}.
- Clientes por categoría: {'FIELD_DIFFERENCES': 155, 'MISSING_PROTECTED_FIELDS': 1}.
- Campos faltantes: {'kindOfPerson': 1, 'regime': 1, 'tipo_identificacion': 1}.
- Campos diferentes: {'digito_verificacion': 112, 'kindOfPerson': 93, 'regime': 156, 'tipo_identificacion': 156}.
- Motivos: {'Existen diferencias entre Alegra y BettaApp; no se fuerza el baseline.': 155, 'Faltan campos protegidos confirmables.': 1}.
- Pares tributarios comparados (sin identificadores): {'regime': [{'pair': {'local': '', 'local_normalized': '', 'remote': '', 'remote_normalized': ''}, 'count': 1}, {'pair': {'local': '', 'local_normalized': '', 'remote': 'COMMON_REGIME', 'remote_normalized': 'COMMON_REGIME'}, 'count': 89}, {'pair': {'local': '', 'local_normalized': '', 'remote': 'SIMPLIFIED_REGIME', 'remote_normalized': 'SIMPLIFIED_REGIME'}, 'count': 66}], 'tipo_identificacion': [{'pair': {'local': '', 'local_normalized': '', 'remote': '', 'remote_normalized': ''}, 'count': 1}, {'pair': {'local': '', 'local_normalized': '', 'remote': 'CC', 'remote_normalized': 'cc'}, 'count': 43}, {'pair': {'local': '', 'local_normalized': '', 'remote': 'NIT', 'remote_normalized': 'nit'}, 'count': 112}], 'kindOfPerson': [{'pair': {'local': 'PERSON_ENTITY', 'local_normalized': 'PERSON_ENTITY', 'remote': '', 'remote_normalized': ''}, 'count': 1}, {'pair': {'local': 'PERSON_ENTITY', 'local_normalized': 'PERSON_ENTITY', 'remote': 'LEGAL_ENTITY', 'remote_normalized': 'LEGAL_ENTITY'}, 'count': 92}, {'pair': {'local': 'PERSON_ENTITY', 'local_normalized': 'PERSON_ENTITY', 'remote': 'PERSON_ENTITY', 'remote_normalized': 'PERSON_ENTITY'}, 'count': 63}], 'digito_verificacion': [{'pair': {'local_state': 'empty', 'remote_state': 'empty'}, 'count': 44}, {'pair': {'local_state': 'empty', 'remote_state': 'present'}, 'count': 101}, {'pair': {'local_state': 'empty', 'remote_state': 'zero'}, 'count': 11}]}.

No se modifican clientes ni campos tributarios. Los casos con diferencias, datos protegidos incompletos o contacto remoto ausente quedan bloqueados.
Las solicitudes externas del comando son exclusivamente GET.

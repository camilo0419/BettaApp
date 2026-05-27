# Formato para crear un producto configurable

Este formato sirve para que Betta Diseño entregue la información de un producto nuevo y el administrador lo cargue desde **Admin productos** sin tocar código.

## 1. Datos generales

| Campo | Respuesta |
|---|---|
| Nombre del producto |  |
| Categoría |  |
| Descripción corta |  |
| Descripción larga |  |
| Producto activo | Sí / No |
| Orden en la página principal |  |
| Imagen principal | Adjuntar archivo |
| Fotos adicionales | Adjuntar archivos |

## 2. Tipo de precio

Seleccionar una opción:

- Manual / requiere revisión
- Área m²
- Por unidad

| Campo | Respuesta |
|---|---|
| Precio base por m² |  |
| Precio base por unidad |  |
| ¿Requiere revisión antes de confirmar precio? | Sí / No |

## 3. Preguntas que debe responder el cliente

| Orden | Pregunta visible | Tipo de campo | Obligatorio | Placeholder | Ayuda | ¿Es ancho? | ¿Es alto? | ¿Es cantidad? |
|---:|---|---|---|---|---|---|---|---|
| 1 |  | Texto corto / Número / Selección / Archivo / etc. | Sí / No |  |  | Sí / No | Sí / No | Sí / No |
| 2 |  |  |  |  |  |  |  |  |

Tipos disponibles:

- Texto corto
- Texto largo
- Número decimal
- Número entero
- Selección única
- Selección múltiple
- Checkbox / Sí-No
- Archivo
- Imagen de referencia
- Color
- Fecha

## 4. Opciones para campos tipo selección

Ejemplo: si la pregunta es **Material**, aquí van las opciones.

| Pregunta | Opción | Orden | Tipo de ajuste | Precio |
|---|---|---:|---|---:|
| Material | Vinilo blanco | 1 | No suma precio | 0 |
| Material | Microperforado | 2 | Valor por m² | 25000 |
| Acabado | Laminado mate | 1 | Valor por m² | 12000 |

Tipos de ajuste:

- No suma precio
- Valor fijo
- Valor por m²
- Valor por unidad
- Porcentaje sobre base

## 5. Observaciones de producción

- Archivos requeridos:
- Restricciones:
- Tiempo estimado de entrega:
- Instalación incluida o adicional:
- Notas comerciales:

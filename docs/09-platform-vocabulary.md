# SECOP II e-Auction — Platform Vocabulary

*The platform's own string table, extracted from the auction site's localization bundle. It is the most reliable enumeration of auction states and error conditions we can obtain without triggering each one against the live system.*

Source: `GET /eauctions.api/v1/localization/GetResourceLocaleString?auctionId=..&languageCode=es-CO&marketCode=..`

Bundle size: **566 keys**, locale `es-CO`.

Regenerate with `python3 tools/extract_vocabulary.py <har-dir> docs/09-platform-vocabulary.md`.

See also: [API contract](01-api-contract.md), [auction mechanics](02-auction-mechanics.md).

## What these strings reveal about the rules

Confidence markers: **CONFIRMED** = the platform served this string. **DERIVED** = our
reading of what it implies. Interpretations below are DERIVED and must be verified
against a live auction before the bot relies on them.

### 1. Validation happens at three independent scopes — DERIVED

The error keys come in three parallel families:

| Scope | Key family | Meaning |
|---|---|---|
| Item | `BidWarn_Desc`, `BidWarn_Asc` | Each individual item's bid must improve on your last bid for that item |
| Lot | `BidWarn_Lot_Value_*`, `BidWarn_Lot_Min_Bid_Diff_*` | Each lot's aggregate must improve, respecting that lot's minimum step |
| Total | `BidWarn_Total_Value_*`, `BidWarn_Total_Min_Bid_Diff_*` | The auction-wide total must improve, respecting the total minimum step |

**Consequence for implementation:** in a multi-lot auction a bid is legal only if it
satisfies *all three* scopes simultaneously. Computing a bid that improves the total
while accidentally violating a single item's constraint produces a rejected bid — and a
rejected bid inside the final minute is a wasted opportunity that cannot be retried
before the clock runs out. The strategy engine must solve the constraint set, not just
subtract a step from the total.

### 2. The first bid is a special case — DERIVED

`BidWarn_Min_Bid_Diff_Desc` states: *"Si es su primer lance, este tiene que ser igual al
precio base del lance. Si no es su primer lance, este tiene que ser inferior a su último
lance y debe respetar el lance mínimo definido."*

Read literally, in a descending auction **your first bid must equal the base price**, and
only subsequent bids move downward by at least the minimum step. If true, the bot must
open at the base price rather than jumping straight to its target — meaning it needs at
least two round trips to reach a competitive price, which changes the timing budget.

The ascending variant (`BidWarn_Min_Bid_Diff_Asc`) is more permissive: *"puede ser igual
al precio base ... o puede aplicar el lance mínimo definido"*. The asymmetry is
suspicious and this rule is a **priority open question**.

### 3. The minimum-step wording is self-contradictory — CONFIRMED quirk

Several strings read *"tiene que ser inferior al último lance **más** la diferencia
mínima total"* — literally "must be lower than the last bid **plus** the minimum
difference". For a descending auction the arithmetic that actually makes sense is:

```
newTotal <= lastTotal - minimumDifference
```

The word *más* appears to be a translation defect in the platform, present in both the
ascending and descending variants. **Do not implement from the prose.** Implement from
`ValidateBid`'s accept/reject behaviour, which is authoritative.

### 4. There is an explicit "auction not running" guard — CONFIRMED

`BidWarn_Auction_Not_In_Progress` ("La subasta ya no está en curso") confirms the server
rejects bids outside the running window. The bot must therefore treat its own clock
estimate as advisory and be ready to handle this rejection gracefully rather than
assuming a bid submitted before its computed deadline will be accepted.

### 5. Decimal precision is enforced — CONFIRMED

`BidWarn_Wrong_Decimal` ("El número de dígitos soportado ha sido ultrapasado") confirms
the `decimalNum` / `totalNum` fields from `GetAuctionSimple` are enforced server-side.
Bid values must be rounded to the auction's declared precision before submission, using
the auction's `roundTypeCode`.

---

# Reference tables

## Bid validation errors

These are the failures `ValidateBid` and `SubmitBid` can return. The API response field `errorMessageLocale` maps directly to these keys, so the bot should branch on the **key**, never on the Spanish prose (which is localised and may change).

| Key | Spanish string |
|---|---|
| `BidWarn_Asc` | Hay valores que no son válidos. Por favor, corrija estos valores. Lance por el artículo debe ser superior a su último lance. |
| `BidWarn_Auction_Not_In_Progress` | La subasta ya no está en curso |
| `BidWarn_Desc` | Hay valores que no son válidos. Por favor, corrija estos valores. Lance por el artículo debe ser inferior a su último lance. |
| `BidWarn_Invalid_Value` | Valor no Válido |
| `BidWarn_Lot_MinBid_Diff_Desc` | El valor del lote tiene que ser inferior al valor del último lance para este lote. La licitación tiene que ser inferior al último lance más la diferencia mínima total ({0}) |
| `BidWarn_Lot_Min_Bid_Diff_Asc` | El valor del lote tiene que ser superior al valor de su último lance para este lote. La licitación tiene que ser superior al último lance más la diferencia mínima total ({0}) |
| `BidWarn_Lot_Min_Bid_Diff_Desc` | El valor del lote tiene que ser inferior al valor del último lance para este lote. La licitación tiene que ser inferior al último lance más la diferencia mínima total ({0}) |
| `BidWarn_Lot_Value_Asc` | El valor del lote deberá ser superior al último lance para ese lote. |
| `BidWarn_Lot_Value_Desc` | El valor del lote deberá ser inferior al último lance para ese lote. |
| `BidWarn_Min_Bid_Diff_Asc` | Valor inválido. Por favor, corrija el valor teniendo en cuenta las siguientes reglas: Si es su primer lance éste puede ser igual al precio base del lance o puede aplicar el lance mínimo definido. Si no es su primer lance, este tiene que ser superior a su último lance y debe respetar el lance mínimo definido. ({0}) |
| `BidWarn_Min_Bid_Diff_Desc` | Valor inválido. Por favor, corrija el valor teniendo en cuenta las siguientes reglas: Si es su primero lance, este tiene que ser igual al precio base del lance. Si no es su primer lance, este tiene que ser inferior a su último lance y debe respetar el lance mínimo definido ({0}) |
| `BidWarn_Sum` | La suma de total de la oferta no es válida. Revise sus ofertas. |
| `BidWarn_Total_MinBid_Diff_Desc` | El valor total tiene que ser inferior al valor de su último lance para la subasta actual. La licitación tiene que ser inferior al último lance más la diferencia mínima total ({0}) |
| `BidWarn_Total_Min_Bid_Diff_Asc` | El valor total tiene que ser superior al valor de su último lance para la subasta actual. La licitación tiene que ser superior al último lance más la diferencia mínima total ({0}) |
| `BidWarn_Total_Min_Bid_Diff_Desc` | El valor total tiene que ser inferior al valor de su último lance para la subasta actual. La licitación tiene que ser inferior al último lance más la diferencia mínima total ({0}) |
| `BidWarn_Total_Value_Asc` | El valor total debe ser superior al valor de su último lance para la subasta actual. |
| `BidWarn_Total_Value_Desc` | El valor total debe ser inferior al valor de su último lance para la subasta actual. |
| `BidWarn_Wrong_Decimal` | El número de dígitos soportado ha sido ultrapasado. |

## Bid and price-list vocabulary

| Key | Spanish string |
|---|---|
| `Bids_List_Bid` | Lance |
| `Bids_List_Company` | Proveedor |
| `Bids_List_Items` | Artículos |
| `Bids_List_List_Bids` | Últimos lances |
| `Bids_List_Perc_Bids` | % de lances |
| `Bids_List_Rank` | Clasificación |
| `Bids_List_Score` | Puntaje |
| `Bids_List_Show_Less` | Menos |
| `Bids_List_Show_More` | Más |
| `Bids_List_Total_Value` | Valor total |
| `Bids_List_Where` | Donde |

## Auction status and connection status

The auction room surfaces these. `ConnectionStatus_*` are what the screen recording displayed as `ESTADO: DESCONECTADO` when the SignalR socket dropped mid-auction.

| Key | Spanish string |
|---|---|
| `ConnectionStatus_Connected` | Conectado |
| `ConnectionStatus_Connecting` | Conectando |
| `ConnectionStatus_Disconnected` | Desconectado |
| `ConnectionStatus_Reconnecting` | Reconectando |
| `Sidebar_Bidders` | Proveedores |
| `Sidebar_Bids` | Lances |
| `Sidebar_Canceled` | Cancelado |
| `Sidebar_ConnectionStatus` | Estado: |
| `Sidebar_Create_Report` | Crear informe |
| `Sidebar_Create_excel_Report` | Crear informe Excel |
| `Sidebar_Create_pdf_Report` | Crear informe PDF |
| `Sidebar_Current_Best_Bid` | Mejor lance actual |
| `Sidebar_Export_Table` | Exportar tabla |
| `Sidebar_Finished` | Terminado |
| `Sidebar_First_Place` | Primer lugar |
| `Sidebar_In_Progress` | En curso |
| `Sidebar_Items` | Artículos |
| `Sidebar_Lots` | Lotes |
| `Sidebar_NotifyA_Report` | Generar oferta  |
| `Sidebar_NotifyB_Report` | Generar oferta y adjudicar |
| `Sidebar_Outbided` | Superado |
| `Sidebar_Paused` | Suspendido |
| `Sidebar_Ready_To_Start` | Listo para comenzar |
| `Sidebar_Savings` | Ahorros |
| `Sidebar_Score` | Puntaje |
| `Sidebar_Settings` | Configuraciones |
| `Sidebar_Winning` | Ganando |
| `Sidebar_Won` | Vencedor |
| `Sidebar_Your_Total` | Su total |
| `StatusAdm_Company` | Proveedor |
| `StatusAdm_Connection` | Conexión |
| `StatusAdm_Date` | Fecha |
| `StatusAdm_Event_Logs` | Registro de eventos |
| `StatusAdm_Last_Connections` | Últimas conexiones |
| `StatusAdm_Logs` | Registros |
| `StatusAdm_Module` | Módulo |
| `StatusAdm_Running` | En curso |
| `StatusAdm_Status` | Estado |
| `StatusAdm_Sys_Status` | Estado del sistema |
| `StatusAdm_System_Health` | Estado del sistema |
| `StatusAdm_User` | Usuario |

## Event log vocabulary

`GET /v1/audit/GetEventLogs` returns audit events described by these keys. They are the primary means of reconciling what happened while the bot was disconnected.

| Key | Spanish string |
|---|---|
| `EventsAdm_Auction` | Subasta |
| `EventsAdm_Customer` | Cliente  |
| `EventsAdm_Date` | Fecha |
| `EventsAdm_Description` | Descripción |
| `EventsAdm_Event` | Evento |
| `EventsAdm_Events` | Registro de eventos |
| `EventsAdm_IP` | Dirección de IP |
| `EventsAdm_Type` | Tipo |
| `EventsAdm_User` | Usuario (proveedor) |
| `Log_Message_AuctionCanceled` | Subasta Cancelada con el siguiente comentario: {0} |
| `Log_Message_AuctionCanceledWithDiffTime` | Subasta cancelada con el siguiente comentario: {0}. Faltaban {1} minutos y {2} segundos para el fin de la subasta. |
| `Log_Message_AuctionExtended` | Subasta Prorrogada por {0} segundos |
| `Log_Message_AuctionFinished` | Subasta Terminada |
| `Log_Message_AuctionPause` | Subasta Suspendida |
| `Log_Message_AuctionPauseWithDiffTime` | La subasta fue suspensa. Faltaban {0} minutos y {1} segundos para el fin de la subasta. |
| `Log_Message_AuctionPause_Time` | Subasta Suspendida (Nueva fecha de inicio {0} y fecha fin {1}) |
| `Log_Message_AuctionPause_Time_Comments` | Subasta Suspendida com el siguiente comentario: {0} |
| `Log_Message_AuctionStart` | Subasta en Curso |
| `Log_Message_BidError` | Error cuando presentado el lance. Problema: {0} |
| `Log_Message_BidError_MinBid` | Error cuando presentado el lance. Problema: {0} |
| `Log_Message_Connected` | Usuario conectado |
| `Log_Message_Disconnected` | Usuario desconectado |
| `Log_Message_FinishParticipation` | Proveedor: {0} Justificación: {1} |
| `Log_Message_Login` | Usuario conectado |
| `Log_Message_Message` | {0} |
| `Log_Message_Message_With_Recipients` | Para: {0} Mensage: {1} |
| `Log_Message_NewAuction` | Crear nueva Subasta |
| `Log_Message_NewBid` | En {0}, se presentó un Lance de {1} |
| `Log_Message_NewBidAll` | Se envió una puja global de {0} |
| `Log_Message_NewBidLot` | Se envió una puja global de {0} |
| `Log_Message_NewMessage` | Crear nueva messaje |
| `Log_Message_NewUser` | Crear nuevo usuario |
| `Log_Message_StartBidding` | Comenzar Lances |
| `Log_Message_SubmitBidding` | Presentar Lances |
| `LogsAdm_Auction` | Subasta |
| `LogsAdm_Customer` | Proveedor |
| `LogsAdm_Date` | Fecha |
| `LogsAdm_Description` | Descripción |
| `LogsAdm_Event` | Evento |
| `LogsAdm_IP` | Dirección de IP |
| `LogsAdm_Logs` | Registros |
| `LogsAdm_Type` | Tipo |
| `Logs_Advanced_Search` | Búsqueda avanzada |
| `Logs_Date` | Fecha |
| `Logs_Description` | Descripción |
| `Logs_Event` | Evento |
| `Logs_IP` | Dirección de IP |
| `Logs_Search` | Búsqueda   |
| `Logs_Search_Announcement` | Anuncio |
| `Logs_Search_AuctionCanceled` | Subasta cancelada |
| `Logs_Search_AuctionDeprecated` | Modificación |
| `Logs_Search_AuctionExtended` | Subasta Prorrogada |
| `Logs_Search_AuctionFinished` | Subasta terminada |
| `Logs_Search_AuctionResumed` | Subasta Reiniciada |
| `Logs_Search_AuctionStart` | Subasta en curso |
| `Logs_Search_AuctionSuspense` | Subasta Suspendida |
| `Logs_Search_Bid` | Lance |
| `Logs_Search_Company` | Entidad Estatal/Proveedor |
| `Logs_Search_Conversation` | Conversación |
| `Logs_Search_EndDate` | Fecha fin |
| `Logs_Search_Event` | Evento |
| `Logs_Search_Filter` | Buscar |
| `Logs_Search_FinishParticipation` | Finalizar Participación |
| `Logs_Search_InitialDate` | Fecha de inicio |
| `Logs_Search_Login` | Iniciar sesión |
| `Logs_Search_Logout` | Cerrar sesión |
| `Logs_Search_Message` | Mensaje |
| `Logs_Search_MessengerLogin` | Iniciar sesión en conversaciones |
| `Logs_Search_MessengerLogout` | Cerrar sesión en conversaciones |
| `Logs_Search_NewAuction` | Nueva Subasta |
| `Logs_Search_NewBid` | Nuevo Lance |
| `Logs_Search_NewBidAll` | Nueva puja global |
| `Logs_Search_NewBidLot` | Nueva puja global |
| `Logs_Search_NewMessage` | Nuevo Mensaje |
| `Logs_Search_NewUser` | Nuevo Usuario |
| `Logs_Search_Reset` | Borrar |
| `Logs_Search_StartBidding` | Comenzar lances |
| `Logs_Search_SubmitBidding` | Presentar lances |
| `Logs_Search_Type` | Tipo |
| `Logs_Type` | Tipo |
| `Logs_User` | Usuario (proveedor) |

## Auction settings vocabulary

The configuration surface of an auction. Mirrors the fields returned by `GET /v1/auction/GetAuctionSimple/{auctionId}`.

| Key | Spanish string |
|---|---|
| `Settings_Activate_Extra_Time_Winner` | Tiempo extra solo para lance ganador |
| `Settings_Allow_Automatic_Extra_time` | Configurar tiempo extra |
| `Settings_Allow_Manual_Extra_time` | Configurar manualmente tiempo extra |
| `Settings_Allow_Pause` | Permitir arranque manual |
| `Settings_Allowed_Bids` | Lances permitidos |
| `Settings_AuctionByItem` | Por artículo |
| `Settings_AuctionByLot` | Por lote |
| `Settings_AuctionByProposal` | Por lista |
| `Settings_Auction_Information` | Información de la subasta |
| `Settings_Auction_Type` | Tipo de subasta |
| `Settings_Auction_Version` | Versión de subasta |
| `Settings_BaseEvaluationPoints` | Artículos calitativos |
| `Settings_Bellow_Best_Bid` | Lances abajo del mejor lance |
| `Settings_Best_Bid_All` | Mejores lances para todos los lances |
| `Settings_Best_Bid_Self` | Mejores lances para mis lances |
| `Settings_Bidders` | Proveedores online |
| `Settings_Bidders_Grid_SetUp` | Configuración de lista de proveedores |
| `Settings_Bidders_Name_For_Auction_Owner` | Nombre de los proveedores para la Entidad Estatal. |
| `Settings_Bidding_Rules` | Reglas de la subasta |
| `Settings_Cancel` | Cancelar |
| `Settings_CancelAuction` | Cancelar subasta |
| `Settings_CancelAuctionDescription` | ¿Está seguro de que desea cancelar la subasta? |
| `Settings_Cancel_Comments` | Justificación para cancelar la subasta |
| `Settings_Comments` | Justificación para suspender la subasta |
| `Settings_Company` | Proveedor |
| `Settings_Confidential` | Confidencial |
| `Settings_Contact` | Contacto |
| `Settings_Copy` | Copiar |
| `Settings_CopyAuction` | Copiar subasta |
| `Settings_CopyAuctionDescription` | ¿Está seguro de que desea copiar la subasta? |
| `Settings_Currency` | Moneda |
| `Settings_Decimal_Num` | Total de decimales |
| `Settings_Edit` | Editar |
| `Settings_EditAuction` | Editar evento |
| `Settings_EditAuctionDescription` | ¿Está seguro de que desea editar la subasta? |
| `Settings_Email` | Correo electrónico |
| `Settings_EndDateTime` | Fecha fin nueva |
| `Settings_End_Date` | Fecha fin  actual |
| `Settings_Evaluation_Formula` | Fórmula de evaluación |
| `Settings_Extend` | Extender/Prorrogar |
| `Settings_Extra_Time_In_The_Last` | Tiempo extra en los últimos |
| `Settings_Extra_Time_Interval` | Tiempo extra minutos |
| `Settings_Extra_Time_Last_Minutes_Start_Period` | Tiempo extra en los últimos minutos |
| `Settings_FinishParticipation` | Finalizar Participación |
| `Settings_FinishParticipationDescription` | ¿Está seguro que desea no seguir participando en la subasta? |
| `Settings_FinishParticipation_Comments` | Justificación de no continuar |
| `Settings_FinishParticipation_Title` | Finalizar participación en la subasta |
| `Settings_FinishParticipation_btn` | Finalizar Participación |
| `Settings_Forward` | Ascendente |
| `Settings_Hide_CurrentBid` | Ocultar último lance |
| `Settings_Hours` | Horas |
| `Settings_InitialBid_ShowBeforeStart` | Valor inicial del lance antes del inicio |
| `Settings_Language_Code` | Código de idioma |
| `Settings_Market_Code` | Código de mercado |
| `Settings_Maximum_Bids_Extra_Time` | Contador de máximo de lances en tiempo extra (el número disminuye siempre que se realiza un lance) |
| `Settings_Maximum_Bids_Extra_Time_AltText` | (Los lances extra no son por proveedor, sino por el total realizados por cualquier proveedor) |
| `Settings_Minutes` | Minutos |
| `Settings_Minutes_Of_The_Auction` | Minutos de la subasta |
| `Settings_NIF` | NIT |
| `Settings_Name` | Nombre |
| `Settings_No` | No  |
| `Settings_NoValueRank` | No mostrar valor en el rankig |
| `Settings_OK` | Sí |
| `Settings_Operation_Invalid_End_Date` | Fecha fin no válida. No es posible cambiar el estado. |
| `Settings_Operation_Invalid_Start_Date` | Fecha de inicio no válida. No es posible cambiar estado |
| `Settings_Operation_Success` | Operación realizada  |
| `Settings_Original_End_Date` | Fecha de fin original |
| `Settings_Original_Start_Date` | Fecha de inicio original |
| `Settings_Owner` | Entidad Estatal |
| `Settings_Pause` | Suspender |
| `Settings_PauseAuction` | Suspender subasta |
| `Settings_PauseAuctionDescription` | ¿Esta seguro que desea suspender la subasta? |
| `Settings_PauseOptions` | Opciones de pausa |
| `Settings_PriceList` | Lista de precios |
| `Settings_QuantitativeItems` | Artículos cuantitativos |
| `Settings_Quantity` | Cantidades |
| `Settings_Rank_Per_Bidding_Line` | Clasificación por línea de lance |
| `Settings_Request_Information` | Número del proceso |
| `Settings_Reversed` | Descendente |
| `Settings_Round_Type` | Tipo de ronda |
| `Settings_See_Other_Bidders_Name` | Ver nombre de otros proveedores |
| `Settings_Show_Final_Ranking` | Mostrar posición final |
| `Settings_Start` | Inicio |
| `Settings_StartAuction` | Comenzar la subasta |
| `Settings_StartAuctionDescription` | ¿Esta seguro que desea comenzar la subasta? |
| `Settings_StartDateTime` | Nueva fecha de inicio |
| `Settings_Start_Date` | Fecha de inicio actual |
| `Settings_Test_Auction` | Subasta de prueba |
| `Settings_TotalEvaluationPoints` | Total de puntos |
| `Settings_Total_Num` | Total de digitos |
| `Settings_VortalTrust` | ¿VORTALtrust? |
| `Settings_Weight` | Peso |
| `Settings_Yes` | Sí |
| `Settings_eAuction_Owner` | Entidad Estatal |

## Chat and participants

| Key | Spanish string |
|---|---|
| `ChatHistory_Error_Char_Exceed` | Límite de 3000 caracteres. |
| `ChatHistory_Export` | Exportar |
| `ChatHistory_New_Message` | Nuevo mensaje |
| `ChatHistory_Online_Bidders` | Proveedores online |
| `ChatHistory_Online_Companies` | Entidad Estatal online |
| `ChatHistory_Send` | Enviar |
| `ChatHistory_Send_To` | Enviar para |
| `Chat_All` | Todos |
| `Chat_Buyer` | Entidad Estatal |
| `Chat_Chat_History` | Historial de conversaciones |
| `Chat_Error_Char_Exceed` | Límite de 3000 caracteres. |
| `Chat_Me` | Yo |
| `Chat_Online_Bidders` | Proveedores online |
| `Chat_Send` | Enviar |

## Omitted

307 further keys cover generic UI chrome, admin-only screens and export labels with no bearing on bot behaviour. Regenerate this file to inspect them.

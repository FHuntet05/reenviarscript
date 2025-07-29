# main.py
import asyncio
import os
import time
import pickle
from telethon.sync import TelegramClient
from telethon.errors.rpcerrorlist import FloodWaitError
from telethon.sessions import StringSession

# --- CONFIGURACIÓN MEDIANTE VARIABLES DE ENTORNO ---
# Estas variables las configurarás en Railway, no aquí.

API_ID = os.environ.get('API_ID')
API_HASH = os.environ.get('API_HASH')
SESSION_STRING = os.environ.get('SESSION_STRING')
SOURCE_CHAT_IDS_STR = os.environ.get('SOURCE_CHAT_IDS') # Ej: "-100111,-100222,-100333"
DESTINATION_CHAT_ID_STR = os.environ.get('DESTINATION_CHAT_ID')
SLEEP_INTERVAL_SECONDS = int(os.environ.get('SLEEP_INTERVAL_SECONDS', 3600)) # 1 hora por defecto

# --- VALIDACIÓN DE LA CONFIGURACIÓN ---
if not all([API_ID, API_HASH, SESSION_STRING, SOURCE_CHAT_IDS_STR, DESTINATION_CHAT_ID_STR]):
    raise ValueError("❌ Faltan una o más variables de entorno: API_ID, API_HASH, SESSION_STRING, SOURCE_CHAT_IDS, DESTINATION_CHAT_ID")

# Convertir IDs de string a lista de enteros
try:
    SOURCE_CHAT_IDS = [int(chat_id.strip()) for chat_id in SOURCE_CHAT_IDS_STR.split(',')]
    DESTINATION_CHAT_ID = int(DESTINATION_CHAT_ID_STR)
    API_ID = int(API_ID)
except ValueError:
    raise ValueError("❌ Asegúrate de que los IDs de los canales y el API_ID sean números enteros.")


# --- LÓGICA PARA EVITAR DUPLICADOS ---
PROCESSED_VIDEOS_FILE = 'processed_videos.dat'

def load_processed_videos():
    """Carga el set de IDs de videos ya procesados."""
    try:
        with open(PROCESSED_VIDEOS_FILE, 'rb') as f:
            return pickle.load(f)
    except (FileNotFoundError, EOFError):
        return set()

def save_processed_videos(processed_set):
    """Guarda el set de IDs de videos procesados."""
    with open(PROCESSED_VIDEOS_FILE, 'wb') as f:
        pickle.dump(processed_set, f)

# Barra de progreso visual
def print_progress(current, total, start_time, new_videos_count):
    progress = int((current / total) * 30) if total > 0 else 0
    bar = '█' * progress + '-' * (30 - progress)
    elapsed = time.time() - start_time
    
    print(
        f"\r📦 Progreso: [{bar}] {current}/{total} | 🎞️ Nuevos Videos: {new_videos_count} | ⏱️ Tiempo: {int(elapsed)}s",
        end=''
    )

async def main():
    """Función principal que se ejecuta en bucle."""
    print("🚀 Iniciando cliente de Telegram con String Session...")
    
    async with TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH) as client:
        me = await client.get_me()
        print(f"✅ Cliente conectado como: {me.first_name}\n")

        try:
            dest_entity = await client.get_entity(DESTINATION_CHAT_ID)
            print(f"🎯 Canal de destino configurado: {getattr(dest_entity, 'title', dest_entity.id)}\n")
        except Exception as e:
            print(f"❌ Error fatal: No se pudo encontrar el canal de destino con ID {DESTINATION_CHAT_ID}. Error: {e}")
            return # Termina el script si el destino no es válido

        source_entities = []
        print("📡 Verificando canales de origen...")
        for source_id in SOURCE_CHAT_IDS:
            try:
                entity = await client.get_entity(source_id)
                source_entities.append(entity)
                print(f"  -> ✅ Origen encontrado: {getattr(entity, 'title', entity.id)}")
            except Exception as e:
                print(f"  -> ⚠️  Advertencia: No se pudo encontrar el canal de origen con ID {source_id}. Se omitirá. Error: {e}")
        
        if not source_entities:
            print("❌ Error fatal: No se encontró ningún canal de origen válido. Saliendo.")
            return

        # Bucle principal para ejecutar la tarea periódicamente
        while True:
            print("\n" + "="*50)
            print(f"🔄 Inciando nuevo ciclo de escaneo: {time.strftime('%Y-%m-%d %H:%M:%S')}")
            
            processed_videos = load_processed_videos()
            print(f"🔍 Se han procesado {len(processed_videos)} videos anteriormente.")
            
            videos_to_forward = []
            
            # 1. Recolectar todos los videos nuevos de todos los canales de origen
            print("🎞️  Buscando nuevos videos en los canales de origen...")
            for source_entity in source_entities:
                print(f"   -> Escaneando en '{getattr(source_entity, 'title', source_entity.id)}'...")
                try:
                    async for msg in client.iter_messages(source_entity):
                        # Solo procesamos videos que no hayan sido enviados antes
                        if msg.video and msg.id not in processed_videos:
                            videos_to_forward.append((msg, source_entity.id)) # Guardamos el mensaje y su origen
                except Exception as e:
                    print(f"      -> ❌ Error al escanear el canal {getattr(source_entity, 'title', source_entity.id)}: {e}")

            # Ordenar por fecha (ID del mensaje) para reenviar en orden cronológico
            videos_to_forward.sort(key=lambda x: x[0].id)
            total_new = len(videos_to_forward)

            if total_new == 0:
                print("✅ No se encontraron videos nuevos en este ciclo.")
            else:
                print(f"📲 Se encontraron {total_new} videos nuevos. Iniciando reenvío...")
                start_time = time.time()
                
                # 2. Reenviar los videos recolectados
                for i, (message, source_id) in enumerate(videos_to_forward, start=1):
                    try:
                        await client.forward_messages(dest_entity, message)
                        processed_videos.add(message.id) # Marcar como procesado
                        print_progress(i, total_new, start_time, total_new)
                        await asyncio.sleep(2)  # Pausa para no sobrecargar la API
                    except FloodWaitError as e:
                        print(f"\n⏳ Esperando {e.seconds} segundos por limitación de Telegram...")
                        await asyncio.sleep(e.seconds)
                    except Exception as e:
                        print(f"\n⚠️ Error reenviando video ID {message.id} desde {source_id}: {e}")
                        await asyncio.sleep(5) # Pausa más larga en caso de otro error
                
                print(f"\n\n✅ ¡Ciclo completado con éxito!")
                print(f"📦 Total de videos copiados en este ciclo: {total_new}")
            
            # 3. Guardar el estado y esperar para el próximo ciclo
            save_processed_videos(processed_videos)
            print(f"💾 Estado guardado. {len(processed_videos)} videos en total procesados.")
            print(f"😴 Durmiendo durante {SLEEP_INTERVAL_SECONDS / 60:.0f} minutos...")
            await asyncio.sleep(SLEEP_INTERVAL_SECONDS)


if __name__ == "__main__":
    asyncio.run(main())
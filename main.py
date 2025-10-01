from fastapi import FastAPI, File, UploadFile, HTTPException, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
import uvicorn
import io
from PIL import Image
import rembg
import time
import logging
import sys
from typing import Dict, List, Tuple
from datetime import datetime, timedelta

# Configuração do logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('nofakepng_api.log', encoding='utf-8')
    ]
)

logger = logging.getLogger("NoFakePNG-API")

app = FastAPI(
    title="NoFakePNG API",
    description="API para remover o background de imagens",
    version="1.0.0"
)

logger.info("NoFakePNG API inicializada com sucesso")


class RateLimiter:
    def __init__(self, requests_per_minute: int = 100):
        self.requests_per_minute = requests_per_minute
        self.request_history: Dict[str, List[datetime]] = {}
        logger.info(f"RateLimiter inicializado com limite de {requests_per_minute} requisições por minuto")
        
    def is_rate_limited(self, client_ip: str) -> Tuple[bool, int]:
        now = datetime.now()
        minute_ago = now - timedelta(minutes=1)
        
        # Inicializa histórico para novo IP
        if client_ip not in self.request_history:
            self.request_history[client_ip] = []
            logger.info(f"Novo cliente registrado: {client_ip}")
        
        # Remove requisições antigas (mais de 1 minuto)
        old_count = len(self.request_history[client_ip])
        self.request_history[client_ip] = [
            req_time for req_time in self.request_history[client_ip] 
            if req_time > minute_ago
        ]
        new_count = len(self.request_history[client_ip])
        
        if old_count > new_count:
            logger.debug(f"Limpeza de histórico para {client_ip}: {old_count - new_count} requisições antigas removidas")
        
        # Verifica se excedeu o limite
        current_requests = len(self.request_history[client_ip])
        if current_requests >= self.requests_per_minute:
            oldest_request = self.request_history[client_ip][0]
            wait_seconds = 60 - (now - oldest_request).total_seconds()
            logger.warning(f"Rate limit excedido para {client_ip}: {current_requests}/{self.requests_per_minute} requisições. Aguardar {max(1, int(wait_seconds))} segundos")
            return True, max(1, int(wait_seconds))
        
        # Registra nova requisição
        self.request_history[client_ip].append(now)
        logger.info(f"Requisição autorizada para {client_ip}: {current_requests + 1}/{self.requests_per_minute}")
        return False, 0


rate_limiter = RateLimiter(requests_per_minute=10)


async def check_rate_limit(request: Request):
    client_ip = request.client.host
    is_limited, wait_seconds = rate_limiter.is_rate_limited(client_ip)
    
    if is_limited:
        logger.error(f"Rate limit aplicado para {client_ip}: bloqueado por {wait_seconds} segundos")
        raise HTTPException(
            status_code=429,
            detail=f"Muitas requisições. Por favor, tente novamente em {wait_seconds} segundos."
        )


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

logger.info("Middleware CORS configurado")

@app.get("/")
async def read_root(request: Request):
    client_ip = request.client.host
    logger.info(f"Acesso ao endpoint raiz por {client_ip}")
    return {"message": "NoFakePNG API - Remova o background das suas imagens"}

@app.post("/remove-background", responses={
    200: {
        "content": {"image/png": {}},
        "description": "Imagem com o background removido",
    },
    400: {
        "description": "Erro de validação do arquivo",
        "content": {
            "application/json": {
                "example": {"detail": "Tipo de arquivo inválido ou arquivo muito grande"}
            }
        }
    },
    413: {
        "description": "Arquivo muito grande",
        "content": {
            "application/json": {
                "example": {"detail": "O arquivo excede o tamanho máximo permitido de 10MB"}
            }
        }
    },
    429: {
        "description": "Muitas requisições",
        "content": {
            "application/json": {
                "example": {"detail": "Muitas requisições. Por favor, tente novamente em 30 segundos."}
            }
        }
    }
})
async def remove_background(file: UploadFile = File(...), request: Request = None, _: None = Depends(check_rate_limit)):
    """
    Remove o background de uma imagem.
    
    - **file**: Arquivo de imagem para processamento (máximo 10MB)
    
    Retorna a imagem processada em formato PNG com fundo transparente.
    """
    
    client_ip = request.client.host if request else "unknown"
    start_time = time.time()
    
    logger.info(f"Iniciando processamento de imagem para cliente {client_ip} - Arquivo: {file.filename}, Tipo: {file.content_type}")
    
    valid_image_types = ["image/jpeg", "image/png", "image/jpg", "image/webp", "image/bmp"]
    
    if file.content_type not in valid_image_types:
        logger.warning(f"Tipo de arquivo inválido rejeitado para {client_ip}: {file.content_type} - Arquivo: {file.filename}")
        raise HTTPException(
            status_code=400,
            detail="Tipo de arquivo inválido. Por favor, envie uma imagem (JPEG, PNG, WebP ou BMP)."
        )
    
    
    MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB
    logger.info(f"Validando tamanho do arquivo para {client_ip} - Limite: {MAX_FILE_SIZE / (1024*1024):.1f}MB")
    
    try:
        # Lê o arquivo em chunks para controlar o tamanho
        file_size = 0
        contents = bytearray()
        
        # Lê em chunks de 1MB
        chunk_size = 1024 * 1024  
        while True:
            chunk = await file.read(chunk_size)
            if not chunk:
                break
            file_size += len(chunk)
            contents.extend(chunk)
            
            # Verifica se excedeu o tamanho máximo
            if file_size > MAX_FILE_SIZE:
                logger.warning(f"Arquivo muito grande rejeitado para {client_ip}: {file_size / (1024*1024):.2f}MB > {MAX_FILE_SIZE / (1024*1024):.1f}MB - Arquivo: {file.filename}")
                raise HTTPException(
                    status_code=413,
                    detail=f"O arquivo excede o tamanho máximo permitido de 10MB."
                )
        
        logger.info(f"Arquivo lido com sucesso para {client_ip}: {file_size / (1024*1024):.2f}MB - Arquivo: {file.filename}")
        
        
        # Validação da imagem
        try:
            logger.info(f"Validando formato de imagem para {client_ip}")
            input_image = Image.open(io.BytesIO(contents))
            _ = input_image.mode
            
            width, height = input_image.size
            logger.info(f"Imagem válida para {client_ip}: {width}x{height} pixels, Modo: {input_image.mode}")
                
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Erro ao validar imagem para {client_ip}: {str(e)} - Arquivo: {file.filename}")
            raise HTTPException(
                status_code=400,
                detail="O arquivo enviado não é uma imagem válida."
            )
        
        # Processamento da imagem
        logger.info(f"Iniciando remoção de background para {client_ip} - Arquivo: {file.filename}")
        processing_start = time.time()
        
        try:
            output_image = rembg.remove(input_image)
            processing_time = time.time() - processing_start
            logger.info(f"Background removido com sucesso para {client_ip} em {processing_time:.2f}s - Arquivo: {file.filename}")
            
            # Conversão para PNG
            img_byte_arr = io.BytesIO()
            output_image.save(img_byte_arr, format='PNG')
            img_byte_arr.seek(0)
            
            total_time = time.time() - start_time
            output_size = len(img_byte_arr.getvalue())
            logger.info(f"Processamento completo para {client_ip}: {total_time:.2f}s total, Saída: {output_size / 1024:.1f}KB - Arquivo: {file.filename}")
            
            return Response(content=img_byte_arr.getvalue(), media_type="image/png")
            
        except Exception as e:
            processing_time = time.time() - processing_start
            logger.error(f"Erro durante remoção de background para {client_ip} após {processing_time:.2f}s: {str(e)} - Arquivo: {file.filename}")
            raise HTTPException(
                status_code=500,
                detail="Erro interno durante o processamento da imagem."
            )
            
    except HTTPException:
        raise
    except Exception as e:
        total_time = time.time() - start_time
        logger.error(f"Erro inesperado para {client_ip} após {total_time:.2f}s: {str(e)} - Arquivo: {file.filename}")
        raise HTTPException(status_code=500, detail="Erro interno do servidor.")

if __name__ == "__main__":
    logger.info("Iniciando servidor NoFakePNG API na porta 8000")
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)

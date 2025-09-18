from fastapi import FastAPI, File, UploadFile, HTTPException, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
import uvicorn
import io
from PIL import Image
import rembg
import time
from typing import Dict, List, Tuple
from datetime import datetime, timedelta

app = FastAPI(
    title="NoFakePNG API",
    description="API para remover o background de imagens",
    version="1.0.0"
)


class RateLimiter:
    def __init__(self, requests_per_minute: int = 10):
        self.requests_per_minute = requests_per_minute
        self.request_history: Dict[str, List[datetime]] = {}
        
    def is_rate_limited(self, client_ip: str) -> Tuple[bool, int]:
        now = datetime.now()
        minute_ago = now - timedelta(minutes=1)
        
        
        if client_ip not in self.request_history:
            self.request_history[client_ip] = []
        
        
        self.request_history[client_ip] = [
            req_time for req_time in self.request_history[client_ip] 
            if req_time > minute_ago
        ]
        
        
        if len(self.request_history[client_ip]) >= self.requests_per_minute:
            
            oldest_request = self.request_history[client_ip][0]
            wait_seconds = 60 - (now - oldest_request).total_seconds()
            return True, max(1, int(wait_seconds))
        
        
        self.request_history[client_ip].append(now)
        return False, 0


rate_limiter = RateLimiter(requests_per_minute=10)


async def check_rate_limit(request: Request):
    client_ip = request.client.host
    is_limited, wait_seconds = rate_limiter.is_rate_limited(client_ip)
    
    if is_limited:
        raise HTTPException(
            status_code=429,
            detail=f"Muitas requisições. Por favor, tente novamente em {wait_seconds} segundos."
        )


app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://front-ui-production.up.railway.app"],  
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
async def read_root():
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
async def remove_background(file: UploadFile = File(...), _: None = Depends(check_rate_limit)):
    """
    Remove o background de uma imagem.
    
    - **file**: Arquivo de imagem para processamento (máximo 10MB)
    
    Retorna a imagem processada em formato PNG com fundo transparente.
    """
    
    valid_image_types = ["image/jpeg", "image/png", "image/jpg", "image/webp", "image/bmp"]
    
    if file.content_type not in valid_image_types:
        raise HTTPException(
            status_code=400,
            detail="Tipo de arquivo inválido. Por favor, envie uma imagem (JPEG, PNG, WebP ou BMP)."
        )
    
    
    MAX_FILE_SIZE = 10 * 1024 * 1024  
    
    try:
        
        file_size = 0
        contents = bytearray()
        
        
        chunk_size = 1024 * 1024  
        while True:
            chunk = await file.read(chunk_size)
            if not chunk:
                break
            file_size += len(chunk)
            contents.extend(chunk)
            
            
            if file_size > MAX_FILE_SIZE:
                raise HTTPException(
                    status_code=413,
                    detail=f"O arquivo excede o tamanho máximo permitido de 10MB."
                )
        
        
        try:
            input_image = Image.open(io.BytesIO(contents))
            _ = input_image.mode
            
            width, height = input_image.size
            MAX_DIMENSION = 4000
            
            if width > MAX_DIMENSION or height > MAX_DIMENSION:
                raise HTTPException(
                    status_code=400,
                    detail=f"A imagem é muito grande. As dimensões máximas permitidas são {MAX_DIMENSION}x{MAX_DIMENSION} pixels."
                )
                
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(
                status_code=400,
                detail="O arquivo enviado não é uma imagem válida."
            )
            
        output_image = rembg.remove(input_image)
        
        img_byte_arr = io.BytesIO()
        output_image.save(img_byte_arr, format='PNG')
        img_byte_arr.seek(0)
        
        return Response(content=img_byte_arr.getvalue(), media_type="image/png")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)

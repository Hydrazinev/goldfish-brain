import sys
sys.path.insert(0, 'src')

import cv2
from recogniser import FaceRecogniser, FaceIndex

img = cv2.imread('data/last_capture.jpg')
print('Image shape:', img.shape)

r = FaceRecogniser()
detections = r.detect_faces(img)
print('Faces detected:', len(detections))

if detections:
    crop, bbox, face = detections[0]
    emb = r.get_embedding(img, face)
    print('Embedding shape:', emb.shape if emb is not None else None)

    idx = FaceIndex()
    idx.load('data/shows/breaking-bad/embeddings.faiss')
    results = idx.search(emb, top_k=3)
    print('Top matches:')
    for char_id, score in results:
        print(f'  {char_id}: {score:.4f}')

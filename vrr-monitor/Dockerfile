FROM node:18-alpine

WORKDIR /app

# Copy dependency manifest
COPY package.json ./

# Install production dependencies
RUN npm install --omit=dev

# Copy application files
COPY server.js ./
COPY public/ ./public/

# Expose server port
EXPOSE 3000

# Start Express server
CMD ["node", "server.js"]

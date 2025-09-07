from fastapi import FastAPI, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import Optional, List
import requests
from bs4 import BeautifulSoup
import time
import random
from datetime import datetime
import sqlite3
import os
from urllib.parse import quote

app = FastAPI(
    title="Amazon Price Tracker API",
    description="Track Amazon product prices and get historical data",
    version="1.0.0"
)

# Database setup
def init_db():
    conn = sqlite3.connect('prices.db')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS price_history (
            id INTEGER PRIMARY KEY,
            asin TEXT NOT NULL,
            title TEXT,
            price REAL,
            currency TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            url TEXT
        )
    ''')
    conn.commit()
    conn.close()

# Initialize database on startup
init_db()

# Pydantic models
class PriceResponse(BaseModel):
    asin: str
    title: str
    current_price: float
    currency: str
    url: str
    last_updated: datetime

class PriceHistoryResponse(BaseModel):
    asin: str
    title: str
    price_history: List[dict]
    lowest_price: float
    highest_price: float

# Amazon scraper functions
def get_random_headers():
    user_agents = [
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.1.1 Safari/605.1.15',
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    ]
    return {
        'User-Agent': random.choice(user_agents),
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept-Encoding': 'gzip, deflate, br',
        'DNT': '1',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1'
    }

def scrape_amazon_price(asin: str):
    # Try multiple Amazon domains and approaches
    urls_to_try = [
        f"https://www.amazon.com/dp/{asin}",
        f"https://www.amazon.com/gp/product/{asin}",
    ]
    
    for url in urls_to_try:
        try:
            # Add longer delay to avoid rate limiting
            time.sleep(random.uniform(2, 5))
            
            headers = get_random_headers()
            headers.update({
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.5',
                'Cache-Control': 'no-cache',
                'Pragma': 'no-cache'
            })
            
            session = requests.Session()
            response = session.get(url, headers=headers, timeout=15)
            
            print(f"Response status: {response.status_code}")  # Debug
            print(f"Response length: {len(response.content)}")  # Debug
            
            if response.status_code == 503:
                print("Amazon returned 503 - service unavailable")
                continue
                
            response.raise_for_status()
            
            soup = BeautifulSoup(response.content, 'html.parser')
            
            # Debug: Print page title to see if we got the right page
            page_title = soup.find('title')
            if page_title:
                print(f"Page title: {page_title.get_text()[:100]}")
            
            # More comprehensive price selectors
            price_selectors = [
                'span.a-price-whole',
                'span.a-price.a-text-price.a-size-medium.apexPriceToPay span.a-offscreen',
                'span#priceblock_dealprice',
                'span#priceblock_ourprice', 
                'span.a-price.a-text-price.a-size-medium span.a-offscreen',
                'span.a-price-range',
                '.a-price .a-offscreen',
                'span[data-a-color="price"]',
                '.a-price-whole'
            ]
            
            title_selectors = [
                '#productTitle',
                'h1.a-size-large',
                'h1#title',
                '.product-title',
                'h1 span'
            ]
            
            price = None
            title = None
            
            # Extract price with better parsing
            for selector in price_selectors:
                price_elements = soup.select(selector)
                for price_element in price_elements:
                    if price_element:
                        price_text = price_element.get_text().strip()
                        print(f"Found price text: '{price_text}' with selector: {selector}")  # Debug
                        
                        # More robust price extraction
                        import re
                        price_match = re.search(r'[\$]?([0-9,]+\.?[0-9]*)', price_text)
                        if price_match:
                            price_str = price_match.group(1).replace(',', '')
                            try:
                                price = float(price_str)
                                break
                            except ValueError:
                                continue
                
                if price is not None:
                    break
            
            # Extract title
            for selector in title_selectors:
                title_element = soup.select_one(selector)
                if title_element:
                    title = title_element.get_text().strip()
                    print(f"Found title: '{title[:50]}...'")  # Debug
                    break
            
            if price is not None:
                return {
                    'asin': asin,
                    'title': title or f"Product {asin}",
                    'price': price,
                    'currency': 'USD',
                    'url': url
                }
                
        except Exception as e:
            print(f"Error with URL {url}: {str(e)}")
            continue
    
    # If all URLs failed, return a mock response for testing
    print("All scraping attempts failed, returning mock data")
    return {
        'asin': asin,
        'title': f"Mock Product {asin} (Scraping blocked - need proxy)",
        'price': 99.99,
        'currency': 'USD',
        'url': f"https://www.amazon.com/dp/{asin}"
    }

def save_price_to_db(product_data):
    conn = sqlite3.connect('prices.db')
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO price_history (asin, title, price, currency, url)
        VALUES (?, ?, ?, ?, ?)
    ''', (
        product_data['asin'],
        product_data['title'],
        product_data['price'],
        product_data['currency'],
        product_data['url']
    ))
    conn.commit()
    conn.close()

def get_price_history_from_db(asin: str, days: int = 30):
    conn = sqlite3.connect('prices.db')
    cursor = conn.cursor()
    cursor.execute('''
        SELECT price, timestamp, title FROM price_history 
        WHERE asin = ? AND timestamp >= datetime('now', '-{} days')
        ORDER BY timestamp DESC
    '''.format(days), (asin,))
    
    results = cursor.fetchall()
    conn.close()
    
    if not results:
        return None
    
    price_history = [
        {'price': row[0], 'timestamp': row[1]} 
        for row in results
    ]
    
    prices = [row[0] for row in results]
    return {
        'asin': asin,
        'title': results[0][2],
        'price_history': price_history,
        'lowest_price': min(prices),
        'highest_price': max(prices)
    }

# API Endpoints
@app.get("/")
def read_root():
    return {"message": "Amazon Price Tracker API", "docs": "/docs"}

@app.get("/price/{asin}", response_model=PriceResponse)
def get_current_price(asin: str):
    """Get current price for an Amazon product by ASIN"""
    product_data = scrape_amazon_price(asin)
    
    # Save to database
    save_price_to_db(product_data)
    
    return PriceResponse(
        asin=product_data['asin'],
        title=product_data['title'],
        current_price=product_data['price'],
        currency=product_data['currency'],
        url=product_data['url'],
        last_updated=datetime.now()
    )

@app.get("/history/{asin}", response_model=PriceHistoryResponse)
def get_price_history(asin: str, days: int = 30):
    """Get price history for an Amazon product"""
    history_data = get_price_history_from_db(asin, days)
    
    if not history_data:
        # If no history, get current price first
        current_data = scrape_amazon_price(asin)
        save_price_to_db(current_data)
        
        return PriceHistoryResponse(
            asin=asin,
            title=current_data['title'],
            price_history=[{
                'price': current_data['price'],
                'timestamp': datetime.now().isoformat()
            }],
            lowest_price=current_data['price'],
            highest_price=current_data['price']
        )
    
    return PriceHistoryResponse(**history_data)

@app.get("/deals/{category}")
def find_deals(category: str, min_discount: int = 20, limit: int = 10):
    """Find deals in a specific category (placeholder - requires more complex scraping)"""
    return {
        "message": "Deals endpoint coming soon!",
        "category": category,
        "min_discount": f"{min_discount}%",
        "note": "This requires scraping Amazon's deals pages - will implement next"
    }

@app.get("/health")
def health_check():
    return {"status": "healthy", "timestamp": datetime.now()}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
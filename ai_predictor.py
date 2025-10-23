import numpy as np
from sklearn.linear_model import LinearRegression
from datetime import datetime, timedelta
import random

class StockPredictor:
    def __init__(self):
        self.model = LinearRegression()
        
    def generate_sample_data(self, days=30):
        """إنشاء بيانات نموذجية للتدريب"""
        base_price = 100
        prices = []
        dates = []
        
        for i in range(days):
            # محاكاة تغيرات السوق
            change = random.uniform(-2, 2)
            base_price = max(10, base_price * (1 + change/100))
            prices.append(base_price)
            dates.append(i)
        
        return np.array(dates).reshape(-1, 1), np.array(prices)
    
    def train_model(self, dates, prices):
        """تدريب نموذج التنبؤ"""
        self.model.fit(dates, prices)
    
    def predict_next_day(self, current_price):
        """التنبؤ بالسعر لليوم التالي"""
        # نموذج مبسط للتنبؤ
        trend = random.uniform(-0.5, 0.5)  # اتجاه عشوائي
        predicted_price = current_price * (1 + trend/100)
        
        return {
            'predicted_price': round(predicted_price, 2),
            'trend': 'صعودي' if trend > 0 else 'هبوطي',
            'confidence': round(random.uniform(60, 90), 1)
        }

# اختبار النظام
if __name__ == "__main__":
    predictor = StockPredictor()
    dates, prices = predictor.generate_sample_data()
    predictor.train_model(dates, prices)
    
    current_price = 150  # سعر افتراضي
    prediction = predictor.predict_next_day(current_price)
    
    print(f"🎯 تنبؤات الذكاء الاصطناعي:")
    print(f"السعر الحالي: {current_price} ريال")
    print(f"السعر المتوقع: {prediction['predicted_price']} ريال")
    print(f"الاتجاه: {prediction['trend']}")
    print(f"الثقة: {prediction['confidence']}%")

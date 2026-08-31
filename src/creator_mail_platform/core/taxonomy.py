from __future__ import annotations


COLLABORATION_TAXONOMY = {
    "Beauty & Personal Care": (
        "Skincare", "Makeup", "Haircare", "Fragrance", "Personal Care",
    ),
    "Fashion": (
        "Womenswear", "Menswear", "Shoes", "Bags & Accessories", "Jewelry & Watches",
    ),
    "Home & Lifestyle": (
        "Home Decor", "Kitchen & Dining", "Cleaning & Organization", "Furniture", "Daily Lifestyle",
    ),
    "Technology & Electronics": (
        "Mobile & Accessories", "Audio", "Cameras & Creator Gear", "Computers", "Smart Home",
    ),
    "Gaming & Apps": (
        "Mobile Games", "PC Games", "Console Games", "Apps & Software", "Esports & Streaming",
    ),
    "Parenting & Family": (
        "Pregnancy & Baby Care", "Kids Fashion & Toys", "Family Lifestyle", "Education", "Household Products",
    ),
    "Health, Fitness & Wellness": (
        "Fitness & Training", "Nutrition", "Mental Wellness", "Healthcare", "Yoga & Mindfulness",
    ),
    "Sports & Outdoor": (
        "Running", "Hiking & Camping", "Cycling", "Water Sports", "Team Sports", "Skateboarding",
    ),
    "Food & Beverage": (
        "Restaurants", "Snacks & Drinks", "Cooking", "Kitchen Products", "Food Delivery",
    ),
    "Travel & Local Experiences": (
        "Hotels", "Destinations", "Tours & Activities", "Events & Entertainment", "Local Services",
    ),
    "Automotive & Mobility": (
        "Cars", "Motorcycles", "EVs", "Auto Accessories", "Urban Mobility",
    ),
    "Education, Business & Finance": (
        "Education", "Careers", "Entrepreneurship", "Personal Finance", "Business Tools",
    ),
    "Entertainment, Music & Arts": (
        "Music", "Film & TV", "Books", "Comedy", "Art & Design",
    ),
    "Pets & Animals": (
        "Dogs", "Cats", "Pet Care", "Pet Products", "Other Animals",
    ),
    "Other": (),
}

COLLABORATION_CATEGORIES = tuple(COLLABORATION_TAXONOMY)
CONTACT_METHODS = ("WhatsApp", "Telegram", "LINE", "WeChat", "Other")

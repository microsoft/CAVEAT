function returnShops(){
    let shops = [
        {
            "name":"BJ's Wholesale Club",
            "img":"https://www.instacart.com/image-server/72x/www.instacart.com/assets/domains/warehouse/logo/26/67dffcb3-b227-416a-bc75-8ce2ca10fd92.png"
        }
        ,
        {
            "name":"Hannaford Supermarket",
            "img":"https://www.instacart.com/image-server/72x/www.instacart.com/assets/domains/warehouse/logo/289/fce4ae81-9346-4090-9ed2-1ee7262991f2.png"
        }
        ,
        {
            "name":"Shaw's",
            "img":"https://www.instacart.com/image-server/72x/www.instacart.com/assets/domains/warehouse/logo/375/195ff19d-0f0f-4ff8-969c-9109dbee12c7.png"
        }
        ,
        {
            "name":"7-Eleven",
            "img":"https://www.instacart.com/image-server/72x/www.instacart.com/assets/domains/warehouse/logo/1390/579cb2af-b731-4cec-826d-bc1c1ecbf335.png"
        }
        ,
        {
            "name":"ALDI",
            "img":"https://www.instacart.com/image-server/72x/www.instacart.com/assets/domains/warehouse/logo/12/2774c343-1319-4720-a54f-0c46e93f956c.png"
        }
    ]
    return shops
}

function returnCatgories(){
    let categories = [
        {
            "name":"Grocery",
            "bg_img":"https://www.instacart.com/assets/hubs/category_grocery_desktop-55e4a66309ac045b399fdb52a1a2bd0a8d0b2c31e7aa2305e167344709ca806d.jpg",
            "img":"https://www.instacart.com/image-server/45x/www.instacart.com/assets/home/category_filters/Grocery-circle@3x-753430eb5899d034e3d0b7a06e4112164cfb60ff8c0f8164c362d0466f106eff.png"
        }
        ,
        {
            "name":"Convenience",
            "bg_img":"https://www.instacart.com/assets/hubs/category_convience_desktop-71dab4eed60f07ed3d65523d424d2b90b826ebbe05bd6d1c5978d6f599060971.jpg",
            "img":"https://www.instacart.com/image-server/45x/www.instacart.com/assets/home/category_filters/Convenience-circle@3x-f248b0c45d01c8ac06439d967082c84a077f4b4d4608f9601f2210f2a4eacdb0.png"
        },
        {
            "name":"Alcohol",
            "bg_img":"https://www.instacart.com/assets/hubs/category_alcohol_desktop-79706211db4bd6be788a8f41a840ba2b82e0691764638c070c2990ba37a667cb.jpg",
            "img":"https://www.instacart.com/image-server/45x/www.instacart.com/assets/home/category_filters/Alcohol-circle@3x-6440aef793c995064612afb0713e1c2be5ff257cb16301b722ca40b1fa51b305.png"
        },
        {
            "name":"In-store prices",
            "bg_img":"https://www.instacart.com/assets/hubs/category_grocery_desktop-55e4a66309ac045b399fdb52a1a2bd0a8d0b2c31e7aa2305e167344709ca806d.jpg",
            "img":"https://www.instacart.com/image-server/45x/www.instacart.com/assets/home/category_filters/InStorePricesReupload@3x-81cf88ccbd09c691bc330261b427e1d7870ffd09730e2dfceaf77d27802c8ba4.png"
        },
        {
            "name":"EBT",
            "bg_img":"https://www.instacart.com/assets/hubs/category_ebt_desktop-67bb394d8819f7155eb3160ddee9c618ce167af9bb059e58ebd1f9c8deb0321b.jpg",
            "img":"https://www.instacart.com/image-server/45x/www.instacart.com/assets/home/category_filters/EBT-circle@3x-84e45e294beca9cb5b9ea378b1d0903ca686246edad9d361c977d518169332ff.png"
        },
        {
            "name":"Dollar Store",
            "bg_img":"https://www.instacart.com/assets/hubs/category_dollar-stores_desktop-8e5cb66b76e36b2d25272758eea0c3b513693e287c4f347f06c4947c129732e8.jpg",
            "img":"https://www.instacart.com/image-server/45x/www.instacart.com/assets/home/category_filters/Discount-circle@3x-cd5809b2e67b9cc6deeb32cbf8dbdc5f6ae25f825f23c204e69b83906777a279.png"
        },
        {
            "name":"Pets",
            "bg_img":"https://www.instacart.com/assets/hubs/category_pets_desktop-59a4bc8c69cfbde2efe73497454bcb37250ee0dcb600dda20ad6612d7f55c3a2.jpg",
            "img":"https://www.instacart.com/image-server/45x/www.instacart.com/assets/home/category_filters/Pets-circle@3x-3de17d8f5679f4701a9ca3a1777a9816ff1620f7e7c6c52f6494f16282d51311.png"
        },
        {
            "name":"Stock Up",
            "bg_img":"https://www.instacart.com/assets/hubs/category_bulk_desktop-bc82376429aab0129b39f823862ad0739b07f9455e8950f9f18d43999bd6cba4.jpg",
            "img":"https://www.instacart.com/image-server/45x/www.instacart.com/assets/home/category_filters/Bulk-circle@3x-df2c4f58ad2845b152f79c923c0c2b899012de1c0d7e2a25583e808254cf8693.png"
        },
        {
            "name":"Retail",
            "bg_img":"https://www.instacart.com/assets/hubs/category_retail_desktop-c02a454e17f3a9ccb7f5a0a5f9f343baa72b30d9f7b3e6438f8e48893a035a2a.jpg",
            "img":"https://www.instacart.com/image-server/45x/www.instacart.com/assets/home/category_filters/Retail-circle@3x-b317739a644d186c4226fbf647c905e0b1bb6f7a028e0b2041177a5ad27db580.png"
        },
        {
            "name":"Pharmacy",
            "bg_img":"https://www.instacart.com/assets/hubs/category_pharmacy_desktop-fdd793d4df4011d83a1587623f15ba3fc13e9e8dd0e29aaeaa44f4175a8615fd.jpg",
            "img":"https://www.instacart.com/image-server/45x/www.instacart.com/assets/home/category_filters/Pharmacy-circle@3x-1bed85c24ca37cfc9c10b88da3702d6ce39e479ee1ad68667802294d14ccea09.png"
        }
    ]
    return categories
}

export {returnCatgories,returnShops};
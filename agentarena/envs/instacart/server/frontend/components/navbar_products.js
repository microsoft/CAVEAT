function navBar(){
    return `
        <div class="header">
        <img src="https://www.instacart.com/image-server/120x24/www.instacart.com/assets/beetstrap/brand/instacart-logo-color%403x-586fdf73b07dc9ca4b2c9a57f85f82c46f35debd4fd1887227b83f68e41d4f87.png" alt="">
        <div class="search-container">
            <input type="text" id="searchBar" placeholder="Search products, stores, and recipes">
            <button type="submit"><i class="fa fa-search"></i></button>
                <div class="debouncing">
        
                </div>
        </div>
        <div class="side_buttons">
            <button type="submit">Login</button>
            <i id="cart" class="fa fa-shopping-cart">
            <span id="quantity"></span>
            </i>
        </div>
        </div>
        <hr>
    `
}

export default navBar;
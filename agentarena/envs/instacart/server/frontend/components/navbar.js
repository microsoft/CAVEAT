function navBar(){
    return `
    <!-- nav-bar -->
    <nav class="flex-div">       
        <div class="nav-left flex-div">
            <img src="https://kidsklubs.org/assets/menu.png" class="menu-icon">
            <img src="https://www.instacart.com/image-server/180x36/www.instacart.com/assets/beetstrap/brand/instacart-logo-color%403x-586fdf73b07dc9ca4b2c9a57f85f82c46f35debd4fd1887227b83f68e41d4f87.png" class="logo">
            <a href=""><p id="store">< All stores</p></a>
        </div>

        <div class="search-box flex-div">  
             <input type="text" placeholder="Search Sam's Club..." id="searchBar" >
             <img src="https://encrypted-tbn0.gstatic.com/images?q=tbn:ANd9GcTkFO6NlOCo6GidDrOmn5IoMHpDTFqszQePMu_40oMvEBQ1S-UCW7lVeFHGq2RmLmsmkIU&usqp=CAU" id="searchButton">
             <div class="debouncing">
        
                </div>
         </div> 
       
        <div class="nav-right flex-div">
            <div id="addressBox">
            <img src="https://cdn1.iconfinder.com/data/icons/real-estate-84/64/x-24-512.png">
            <p>Address</p>
            </div>
            <button id="login_btn">Log in</button>
            <div id="cart_btn">
            <p id = "quantity">Cart</p>
            <p id="orderItam"></p>
            </div>
        </div>
    </nav>

    <!-- side-Bar(left) -->
        <div class="sidebar_left">
            <div id="first">
                <a href=""><button id="signUp_btn">Sign up</button></a>
                <a href=""><img src="https://png.pngtree.com/png-vector/20190917/ourlarge/pngtree-exit-icon-vectors-png-image_1737870.jpg"><p>Log in</p></a>
            </div>
            <hr>

            <div id="second">
                <a href=""><img src="https://e7.pngegg.com/pngimages/703/597/png-clipart-logo-house-home-house-angle-building-thumbnail.png"><p>Stores</p></a>
                <a href=""><img src="https://encrypted-tbn2.gstatic.com/images?q=tbn:ANd9GcSG5mCyJcWHhuTHmmXqb27efB6SZonrX1HOfHOO-dOy0NE1gDj8"><p>Your account settings</p></a>
                <a href=""><img src="https://encrypted-tbn2.gstatic.com/images?q=tbn:ANd9GcTuGdqMpSc8wEyeG6dGIV47KK1NEvReE5vb7t4yeEp0m3x0Mbp2"><p>Try Instacart Express</p></a>
            </div>
            <hr>

            <button class="closeBtn">Close</button>
        </div>

    <!--side-bar(right) -->
       <div class="sidebar_right">
           <div class="first_div">
            <img src="https://www.instacart.com/image-server/63x/d2lnr5mha7bycj.cloudfront.net/warehouse/logo/314/1d952780-74c6-4fad-9d11-69924f918d22.png" id="supermarket_logo">
            <p id="supermarket_name">Times Supermarket</p>
            <p class="total">12</p>
            <button class="right_closeBtn closeBtn">Close</button>
           </div>
           <hr>

           <div class="second_div">    
           </div>

           <div class="third_div">
               <div class="suggestion">
                   <img src="https://www.instacart.com/image-server/60x/filters:fill(FFF,true):format(jpg)/d2lnr5mha7bycj.cloudfront.net/product-image/file/large_059eee10-3177-4f46-8ed6-dbf8395087f0.jpg">
                   <img src="https://www.instacart.com/image-server/60x/filters:fill(FFF,true):format(jpg)/d2lnr5mha7bycj.cloudfront.net/product-image/file/large_ccae09a3-5486-4760-8697-8eebd90c8965.jpg">
                   <img src="https://www.instacart.com/image-server/60x/filters:fill(FFF,true):format(jpg)/d2lnr5mha7bycj.cloudfront.net/product-image/file/large_0fe5d018-c658-4c93-9f64-0d4ea4cd3087.jpg">
                   <img src="https://www.instacart.com/image-server/60x/filters:fill(FFF,true):format(jpg)/d2lnr5mha7bycj.cloudfront.net/product-image/file/large_5865a5dd-35a5-4784-be7c-36b2ca035652.jpg">
                   <img src="https://www.instacart.com/image-server/60x/filters:fill(FFF,true):format(jpg)/d2lnr5mha7bycj.cloudfront.net/product-image/file/large_a68014eb-8701-42f1-9c32-b32001ff0b0a.png">
                   <img src="https://www.instacart.com/image-server/60x/filters:fill(FFF,true):format(jpg)/d2lnr5mha7bycj.cloudfront.net/product-image/file/large_63f522d0-f22e-4193-929b-149b3d2f003a.jpg">
                   <img src="https://www.instacart.com/image-server/60x/filters:fill(FFF,true):format(jpg)/d2lnr5mha7bycj.cloudfront.net/product-image/file/large_63f522d0-f22e-4193-929b-149b3d2f003a.jpg">
                   <img src="https://www.instacart.com/image-server/60x/filters:fill(FFF,true):format(jpg)/d2lnr5mha7bycj.cloudfront.net/product-image/file/large_fd684229-deaa-4ff5-8fcd-70aa253ae2ce.jpg">
               </div>
               <hr>
              <div class="go_checkout">
               <p>Go to Checkout</p>
               <p class="total total_chartBox">47</p>
              </div>
           </div>       
       </div>
    `
}

export default navBar;
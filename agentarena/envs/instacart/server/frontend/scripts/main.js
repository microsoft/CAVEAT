import {returnShops} from "./data.js";
let shops = returnShops();

let cart_items = JSON.parse(localStorage.getItem("cart_items"))||[];

async function getData(url){
    try{
        let res = await fetch(url);
        let data = await res.json();
        return data
    }
    catch(err){
        console.log(err)
    }
}

function appendData(data,location){
    location.innerHTML = "";
    data.forEach(({strMeal,strMealThumb}) => {
        let div = document.createElement("div");
        let p1 = document.createElement("div");
        let img = document.createElement("img");
        img.src = strMealThumb;
        p1.textContent = strMeal;

        div.append(img,p1);
        location.append(div)
    });
}


function appendProducts(data,location,dropdown){
    let store_names = Object.keys(data);
    for(let i = 0;i<store_names.length;i++){
        // First Heading div.
        let heading_div = document.createElement("div");
        let text_div = document.createElement("div");

        let img1 = document.createElement("img");
        img1.src = shops[i]['img'];
        let p1 = document.createElement("p");
        p1.textContent = store_names[i];
        let p2 = document.createElement("p");
        p2.textContent = "Delivery avalaible"

        text_div.append(p1,p2)
        heading_div.append(img1,text_div)

        heading_div.id = "heading_div"
        let hr = document.createElement("hr");
        // Products content div.

        let div1 = document.createElement("div");
        data[store_names[i]].map((elem)=>{
            let div2 = document.createElement("div");
            let img = document.createElement("img");
            img.src = elem['img'];
            let p2 = document.createElement("p");
            p2.textContent = elem['name'];
            div2.append(img,p2)
            div1.append(div2)
            div2.addEventListener("click",()=>{
                localStorage.setItem("display_obj",JSON.stringify(elem))
                let modal = document.getElementById("myModal");
                let modal_div = document.querySelector(".display_content");
                appendModal(elem,modal_div,dropdown)
                appendModalData(data[store_names[i]])
                modal.style.display = "block";
            })
        })
        location.append(heading_div,div1,hr)
    }
}


function appendModal(data,location,dropdown){

    location.innerHTML = "";
    let div = document.createElement("div");
    let side_div = document.createElement("div");
    let button_div = document.createElement("div");
    dropdown.style.display = "block";
    let p1 = document.createElement("p")

    p1.textContent = data['name'];

    let p2 = document.createElement("p")
    data['price'] = Math.floor(Math.random() * 10) + 1;
    p2.textContent = "$"+data['price'];

    let btn = document.createElement("button")
    btn.id = "cart_btn";
    btn.textContent = "Add to Cart"

    // Add to Cart functionality.
    btn.addEventListener("click",()=>{
        let qty = JSON.parse(localStorage.getItem("qty"));
        qty = Number(qty);
        data['price'] = Number(data['price'])*qty;
        cart_items.push(data);
        localStorage.setItem("cart_items",JSON.stringify(cart_items));
        window.alert("Item added into the cart")
    })


    button_div.append(dropdown,btn)
    let img = document.createElement("img");
    img.src = data['img'];

    side_div.append(p1,p2,button_div)
    div.append(img,side_div)
    div.style.display = "flex"
    div.style.justifyContent = "space-around"
    location.append(div);
}

function appendModalData(data){
    let main_div = document.querySelector(".recommended")
    main_div.innerHTML = "";
    data.forEach(({img,name}) => {
       let div = document.createElement("div");
       let img1 = document.createElement("img");
       img1.src = img;
       let p1 = document.createElement("p");
       p1.textContent = name;
       let btn = document.createElement("button");
       btn.textContent = "Add to Cart";
       btn.addEventListener("click",()=>{
           let price = Math.floor(Math.random() * 10) + 1;
           let obj = {
               "img":img,
               "name":name,
               "price":price
           }
           cart_items.push(obj);
           localStorage.setItem("cart_items",JSON.stringify(cart_items));
           window.alert("Item added into the cart");
           
       })
       div.append(img1,p1,btn);
       main_div.append(div)
    });
}
export {getData,appendData,appendProducts};
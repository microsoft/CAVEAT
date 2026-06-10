function header(term) {

    return `
    <nav id="item">
            <div class="left">
                <div class="menu">
                    <!-- <svg width="24" height="24" viewBox="0 0 24 24" fill="#343538" xmls="http://www.w3.org/2000/svg" size="24" ></svg> -->
                    <img width="24" height="24" viewBox="0 0 24 24" src="https://img.icons8.com/material-rounded/24/000000/menu--v1.png" />
                </div>
                <div class="logo">
                    <a href="index.html">
                        <img src="https://www.instacart.com/assets/beetstrap/brand/instacart-logo-color-4db9d81ca0b7638befdc4bd331f64a2633df790c0b55ef627c99b1ba77af72b7.svg" alt="logo">
                    </a>
                </div>
            </div>
            <div class="right">
                    <button class="login_btn">
                        <span>Log in</span>
                    </button>
                    <button class="signup_btn">
                        <span>Sign up</span>
                    </button>
            </div>
        </nav>
    `
}
export default header
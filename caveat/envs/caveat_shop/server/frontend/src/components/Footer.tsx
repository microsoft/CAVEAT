import { Link } from 'react-router-dom';

export function Footer() {
  const scrollToTop = () => {
    window.scrollTo({ top: 0, behavior: 'smooth' });
  };

  return (
    <footer>
      {/* Back to top */}
      <button
        onClick={scrollToTop}
        className="w-full py-3 text-white text-sm hover:bg-[var(--caveat-shop-header)]"
        style={{ backgroundColor: 'var(--caveat-shop-header-secondary)' }}
      >
        Back to top
      </button>

      {/* Links Section */}
      <div className="footer-section">
        <div className="max-w-6xl mx-auto px-4">
          <div className="grid grid-cols-2 md:grid-cols-4 gap-8">
            {/* Column 1 */}
            <div>
              <h4 className="font-bold mb-3">Get to Know Us</h4>
              <ul className="space-y-2">
                <li><Link to="#">Careers</Link></li>
                {/* <li><Link to="#">Blog</Link></li> */}
                <li><Link to="#">About CAVEAT-Shop</Link></li>
                {/* <li><Link to="#">Investor Relations</Link></li> */}
                {/* <li><Link to="#">CAVEAT-Shop Devices</Link></li> */}
                {/* <li><Link to="#">CAVEAT-Shop Science</Link></li> */}
              </ul>
            </div>

            {/* Column 2 */}
            {/* <div> */}
            {/* <h4 className="font-bold mb-3">Make Money with Us</h4> */}
            {/* <ul className="space-y-2"> */}
            {/* <li><Link to="/gp/seller-account">Sell products on CAVEAT-Shop</Link></li> */}
            {/* <li><Link to="#">Sell apps on CAVEAT-Shop</Link></li> */}
            {/* <li><Link to="#">Become an Affiliate</Link></li> */}
            {/* <li><Link to="#">Advertise Your Products</Link></li> */}
            {/* <li><Link to="#">Self-Publish with Us</Link></li> */}
            {/* <li><Link to="#">Host an CAVEAT-Shop Hub</Link></li> */}
            {/* </ul> */}
            {/* </div> */}

            {/* Column 3 */}
            <div>
              <h4 className="font-bold mb-3">CAVEAT-Shop Payment Products</h4>
              <ul className="space-y-2">
                {/* <li><Link to="#">Shop with Points</Link></li> */}
                <li><Link to="/gift-cards">Reload Your Balance</Link></li>
                {/* <li><Link to="#">CAVEAT-Shop Currency Converter</Link></li> */}
              </ul>
            </div>

            {/* Column 4 */}
            <div>
              <h4 className="font-bold mb-3">Let Us Help You</h4>
              <ul className="space-y-2">
                <li><Link to="/gp/css/account">Your Account</Link></li>
                <li><Link to="/gp/css/order-history">Your Orders</Link></li>
                {/* <li><Link to="#">Shipping Rates & Policies</Link></li> */}
                <li><Link to="/gp/returns">Returns & Replacements</Link></li>
                {/* <li><Link to="#">Manage Your Content and Devices</Link></li> */}
                <li><Link to="/gp/help/customer">Help</Link></li>
              </ul>
            </div>
          </div>
        </div>
      </div>

      {/* Bottom Section */}
      <div className="footer-bottom">
        <div className="max-w-6xl mx-auto px-4">
          <div className="flex items-center justify-center gap-6 mb-4">
            <div className="text-white font-bold text-xl">
              CAVEAT-Shop
            </div>
          </div>
          <div className="flex flex-wrap items-center justify-center gap-4 text-xs">
            {/* <Link to="#" className="text-gray-400 hover:text-white">Conditions of Use</Link> */}
            {/* <Link to="#" className="text-gray-400 hover:text-white">Privacy Notice</Link> */}
            {/* <Link to="#" className="text-gray-400 hover:text-white">Consumer Health Data Privacy Disclosure</Link> */}
            {/* <Link to="#" className="text-gray-400 hover:text-white">Your Ads Privacy Choices</Link> */}
          </div>
          <p className="mt-4 text-xs text-gray-500">
            © 2024 CAVEAT-Shop. This is a demo application for educational purposes.
          </p>
        </div>
      </div>
    </footer>
  );
}
